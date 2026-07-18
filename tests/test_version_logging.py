# tests/test_version_logging.py -- _package_version, _commit_hash
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""setup_logging() stamps every process's startup with these two so a long-running
--loop process (or a stray leftover one from an old checkout/branch) can be
identified from log.txt alone, without needing to know which directory it was
launched from -- see setup_logging's call site for the log line itself."""

import re

import goes_wallpaper as gw


class TestPackageVersion:
    def test_reads_the_checkout_s_pyproject_toml(self):
        assert gw._package_version() == "2.2.0"

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
