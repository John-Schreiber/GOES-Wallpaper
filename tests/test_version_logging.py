# tests/test_version_logging.py -- _package_version, _commit_hash
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""setup_logging() stamps every process's startup with these two so a long-running
--loop process (or a stray leftover one from an old checkout/branch) can be
identified from log.txt alone, without needing to know which directory it was
launched from -- see setup_logging's call site for the log line itself."""

import logging
import re

import pytest

import goes_wallpaper as gw


class TestPackageVersion:
    def test_reads_the_checkout_s_pyproject_toml(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", gw._package_version())

    def test_falls_back_to_installed_metadata_when_pyproject_unparseable(self, monkeypatch):
        def boom(f):
            raise KeyError("version")

        monkeypatch.setattr(gw.tomllib, "load", boom)
        monkeypatch.setattr(gw.importlib.metadata, "version", lambda name: "9.9.9")
        assert gw._package_version() == "9.9.9"

    def test_returns_unknown_when_neither_source_resolves(self, monkeypatch):
        def boom(f):
            raise KeyError("version")

        def not_found(name):
            raise gw.importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(gw.tomllib, "load", boom)
        monkeypatch.setattr(gw.importlib.metadata, "version", not_found)
        assert gw._package_version() == "unknown"


class TestCommitHash:
    def test_returns_this_checkout_s_short_hash(self):
        # Runs the real `git rev-parse --short HEAD` -- this test suite only runs
        # from within a git checkout, so this should always resolve to something.
        result = gw._commit_hash()
        assert result is not None
        assert re.fullmatch(r"[0-9a-f]{4,40}", result)

    def test_returns_none_when_git_is_not_on_path(self, monkeypatch):
        def boom(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(gw.subprocess, "run", boom)
        assert gw._commit_hash() is None

    def test_returns_none_when_not_a_git_checkout(self, monkeypatch):
        class FakeResult:
            returncode = 128
            stdout = ""

        monkeypatch.setattr(gw.subprocess, "run", lambda *a, **k: FakeResult())
        assert gw._commit_hash() is None

    def test_returns_none_on_timeout(self, monkeypatch):
        def boom(*args, **kwargs):
            raise gw.subprocess.TimeoutExpired(cmd="git", timeout=5)

        monkeypatch.setattr(gw.subprocess, "run", boom)
        assert gw._commit_hash() is None


@pytest.fixture
def _restore_root_logger():
    """setup_logging() replaces the root logger's handlers wholesale -- restore
    whatever was there before so this doesn't leak into other tests' logging."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers = original_handlers
    root.setLevel(original_level)


class TestSetupLogging:
    def test_log_to_stdout_false_only_adds_the_file_handler(self, tmp_path, _restore_root_logger):
        cfg = gw.Config(data_dir=tmp_path, log_to_stdout=False)
        gw.setup_logging(cfg)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], gw.RotatingFileHandler)

    def test_log_to_stdout_true_also_adds_a_stream_handler(self, tmp_path, _restore_root_logger):
        cfg = gw.Config(data_dir=tmp_path, log_to_stdout=True)
        gw.setup_logging(cfg)
        root = logging.getLogger()
        assert len(root.handlers) == 2
        assert any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, gw.RotatingFileHandler)
            for h in root.handlers
        )

    def test_log_to_stdout_true_actually_prints_to_stdout(self, tmp_path, capsys, _restore_root_logger):
        cfg = gw.Config(data_dir=tmp_path, log_to_stdout=True)
        gw.setup_logging(cfg)
        logging.info("distinctive marker message xyz123")
        assert "distinctive marker message xyz123" in capsys.readouterr().out
