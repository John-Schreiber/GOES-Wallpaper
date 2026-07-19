# tests/test_platform_linux_kde.py -- KDEPlatform's mockable parsing/mapping logic
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit-tests the parts of platform_linux_kde.py that don't require a live KDE
session: subprocess.run is mocked throughout, so these confirm the Python-side
JSON parsing/building and fill-mode mapping, not the actual Plasma D-Bus/upower/
nmcli behavior (untested on real hardware -- see NEXT_STEPS.md)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from platform_base import PowerState
import platform_linux_kde as klink


@pytest.fixture(autouse=True)
def _clear_qdbus_cache():
    klink._qdbus_binary.cache_clear()
    klink._kwriteconfig_binary.cache_clear()
    yield
    klink._qdbus_binary.cache_clear()
    klink._kwriteconfig_binary.cache_clear()


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestQdbusBinary:
    def test_prefers_qdbus6_over_qdbus(self):
        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            assert klink._qdbus_binary() == "/usr/bin/qdbus6"

    def test_falls_back_to_qdbus(self):
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/qdbus" if name == "qdbus" else None):
            assert klink._qdbus_binary() == "/usr/bin/qdbus"

    def test_none_when_neither_found(self):
        with patch("shutil.which", return_value=None):
            assert klink._qdbus_binary() is None


class TestRunEvaluateScript:
    def test_returns_none_with_no_binary(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None):
            assert platform._run_evaluate_script("print(1);") is None

    def test_returns_stdout_on_success(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout="hello")):
            assert platform._run_evaluate_script("print('hello');") == "hello"

    def test_returns_none_on_nonzero_exit(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(returncode=1, stderr="boom")):
            assert platform._run_evaluate_script("bad();") is None

    def test_returns_none_when_subprocess_raises(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", side_effect=OSError("no dbus")):
            assert platform._run_evaluate_script("print(1);") is None


class TestGetScreenSize:
    def test_override_short_circuits_detection(self):
        platform = klink.KDEPlatform()
        assert platform.get_screen_size(False, 800, 600) == (800, 600)

    def test_falls_back_when_undetectable(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None):
            assert platform.get_screen_size(False, None, None) == klink._FALLBACK_SIZE

    def test_single_screen(self):
        platform = klink.KDEPlatform()
        screens = [{"screen": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout=json.dumps(screens))):
            assert platform.get_screen_size(False, None, None) == (1920, 1080)

    def test_span_all_monitors_computes_bounding_box(self):
        platform = klink.KDEPlatform()
        screens = [
            {"screen": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
            {"screen": 1, "left": 1920, "top": 0, "width": 1280, "height": 1024},
        ]
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout=json.dumps(screens))):
            assert platform.get_screen_size(True, None, None) == (3200, 1080)

    def test_primary_is_lowest_screen_index(self):
        platform = klink.KDEPlatform()
        screens = [
            {"screen": 1, "left": 1920, "top": 0, "width": 1280, "height": 1024},
            {"screen": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout=json.dumps(screens))):
            assert platform.get_screen_size(False, None, None) == (1920, 1080)


class TestListMonitors:
    def test_empty_when_undetectable(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None):
            assert platform.list_monitors() == []

    def test_sorted_left_to_right_with_screen_index_ids(self):
        platform = klink.KDEPlatform()
        screens = [
            {"screen": 1, "left": 1920, "top": 0, "width": 1280, "height": 1024},
            {"screen": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout=json.dumps(screens))):
            monitors = platform.list_monitors()
        assert [m.id for m in monitors] == ["0", "1"]
        assert monitors[0].right == 1920
        assert monitors[1].left == 1920
        assert monitors[1].right == 3200


class TestGetTaskbarHeight:
    def test_zero_when_no_panels(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout="[]")):
            assert platform.get_taskbar_height() == 0

    def test_picks_bottom_panel_on_primary_screen(self):
        platform = klink.KDEPlatform()
        panels = [
            {"screen": 0, "location": "bottom", "height": 44},
            {"screen": 1, "location": "bottom", "height": 30},
            {"screen": 0, "location": "top", "height": 20},
        ]
        screens = [{"screen": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}]

        def fake_run(args, **kwargs):
            script = args[-1]
            if "panelIds" in script:
                return _completed(stdout=json.dumps(panels))
            return _completed(stdout=json.dumps(screens))

        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", side_effect=fake_run):
            assert platform.get_taskbar_height() == 44


class TestApplyWallpaper:
    def test_uses_cli_when_available(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/plasma-apply-wallpaperimage") as which, \
             patch("subprocess.run", return_value=_completed()) as run:
            platform.apply_wallpaper(Path("/tmp/img.jpg"), "fit")
        run.assert_called_once()
        args = run.call_args[0][0]
        assert args[0] == "/usr/bin/plasma-apply-wallpaperimage"
        assert "--fill-mode" in args
        assert "preserveAspectFit" in args
        assert str(Path("/tmp/img.jpg")) in args

    def test_falls_back_to_dbus_when_cli_missing(self):
        platform = klink.KDEPlatform()

        def which(name):
            # CLI not installed, but qdbus6 (used for the D-Bus fallback) is.
            return None if name == "plasma-apply-wallpaperimage" else f"/usr/bin/{name}"

        with patch("shutil.which", side_effect=which), \
             patch("subprocess.run", return_value=_completed(stdout="")) as run:
            platform.apply_wallpaper(Path("/tmp/img.jpg"), "fill")
        run.assert_called_once()
        script = run.call_args[0][0][-1]
        assert "org.kde.image" in script
        assert "FillMode', 2" in script

    def test_falls_back_to_dbus_when_cli_fails(self):
        platform = klink.KDEPlatform()
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if "plasma-apply-wallpaperimage" in args[0]:
                return _completed(returncode=1, stderr="nope")
            return _completed(stdout="")

        def which(name):
            return "/usr/bin/plasma-apply-wallpaperimage" if name == "plasma-apply-wallpaperimage" else f"/usr/bin/{name}"

        with patch("shutil.which", side_effect=which), \
             patch("subprocess.run", side_effect=fake_run):
            platform.apply_wallpaper(Path("/tmp/img.jpg"), "fill")
        assert len(calls) == 2
        assert calls[1][-1] and "org.kde.image" in calls[1][-1]

    def test_span_style_degrades_to_fill_via_cli(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/plasma-apply-wallpaperimage"), \
             patch("subprocess.run", return_value=_completed()) as run:
            platform.apply_wallpaper(Path("/tmp/img.jpg"), "span")
        args = run.call_args[0][0]
        assert "preserveAspectCrop" in args


class TestApplyWallpaperPerMonitor:
    def test_noop_on_empty_assignments(self):
        platform = klink.KDEPlatform()
        with patch("subprocess.run") as run:
            platform.apply_wallpaper_per_monitor({}, "fill")
        run.assert_not_called()

    def test_builds_script_with_screen_indices_and_fill_mode(self):
        platform = klink.KDEPlatform()
        assignments = {"0": Path("/tmp/a.jpg"), "1": Path("/tmp/b.jpg")}
        with patch("shutil.which", return_value="/usr/bin/qdbus6"), \
             patch("subprocess.run", return_value=_completed(stdout="")) as run:
            platform.apply_wallpaper_per_monitor(assignments, "tile")
        script = run.call_args[0][0][-1]
        payload_json = script.split("var assignments = ", 1)[1].split(";", 1)[0]
        payload = json.loads(payload_json)
        assert {p["screen"] for p in payload} == {0, 1}
        assert all(p["fillMode"] == 3 for p in payload)


class TestGetPowerState:
    def test_none_when_upower_missing(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None):
            assert platform.get_power_state() == PowerState(on_battery=None)

    def test_discharging_with_percentage(self):
        platform = klink.KDEPlatform()
        stdout = "  state:               discharging\n  percentage:          72%\n"
        with patch("shutil.which", return_value="/usr/bin/upower"), \
             patch("subprocess.run", return_value=_completed(stdout=stdout)):
            state = platform.get_power_state()
        assert state.on_battery is True
        assert state.battery_percent == 72.0

    def test_fully_charged_is_not_on_battery(self):
        platform = klink.KDEPlatform()
        stdout = "  state:               fully-charged\n  percentage:          100%\n"
        with patch("shutil.which", return_value="/usr/bin/upower"), \
             patch("subprocess.run", return_value=_completed(stdout=stdout)):
            state = platform.get_power_state()
        assert state.on_battery is False


class TestIsNetworkMetered:
    def test_none_when_nmcli_missing(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None):
            assert platform.is_network_metered() is None

    def test_true_when_metered(self):
        platform = klink.KDEPlatform()

        def fake_run(args, **kwargs):
            if args[-1] == "device":
                return _completed(stdout="wlan0:connected\n")
            return _completed(stdout="yes\n")

        with patch("shutil.which", return_value="/usr/bin/nmcli"), \
             patch("subprocess.run", side_effect=fake_run):
            assert platform.is_network_metered() is True

    def test_false_when_guess_no(self):
        platform = klink.KDEPlatform()

        def fake_run(args, **kwargs):
            if args[-1] == "device":
                return _completed(stdout="eth0:connected\n")
            return _completed(stdout="guess-no\n")

        with patch("shutil.which", return_value="/usr/bin/nmcli"), \
             patch("subprocess.run", side_effect=fake_run):
            assert platform.is_network_metered() is False

    def test_none_when_no_connected_device(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/nmcli"), \
             patch("subprocess.run", return_value=_completed(stdout="wlan0:disconnected\n")):
            assert platform.is_network_metered() is None


class TestKwriteconfigBinary:
    def test_prefers_kwriteconfig6_over_kwriteconfig5(self):
        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            assert klink._kwriteconfig_binary() == "/usr/bin/kwriteconfig6"

    def test_falls_back_to_kwriteconfig5(self):
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/kwriteconfig5" if name == "kwriteconfig5" else None):
            assert klink._kwriteconfig_binary() == "/usr/bin/kwriteconfig5"

    def test_none_when_neither_found(self):
        with patch("shutil.which", return_value=None):
            assert klink._kwriteconfig_binary() is None


class TestSupportsLockScreen:
    def test_true_when_kwriteconfig_found(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/kwriteconfig6"):
            assert platform.supports_lock_screen() is True

    def test_false_when_kwriteconfig_missing(self):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None):
            assert platform.supports_lock_screen() is False


class TestApplyLockScreen:
    def test_noop_with_warning_when_no_binary(self, tmp_path):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value=None), \
             patch("subprocess.run") as run:
            platform.apply_lock_screen(tmp_path / "wallpaper.jpg")
        run.assert_not_called()

    def test_writes_expected_kconfig_group_and_uri(self, tmp_path):
        platform = klink.KDEPlatform()
        path = tmp_path / "wallpaper.jpg"
        with patch("shutil.which", return_value="/usr/bin/kwriteconfig6"), \
             patch("subprocess.run", return_value=_completed()) as run:
            platform.apply_lock_screen(path)

        args = run.call_args[0][0]
        assert args[0] == "/usr/bin/kwriteconfig6"
        assert args[1:] == [
            "--file", "kscreenlockerrc",
            "--group", "Greeter", "--group", "Wallpaper",
            "--group", "org.kde.image", "--group", "General",
            "--key", "Image", path.as_uri(),
        ]

    def test_logs_warning_on_nonzero_exit(self, tmp_path):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/kwriteconfig6"), \
             patch("subprocess.run", return_value=_completed(returncode=1, stderr="boom")):
            platform.apply_lock_screen(tmp_path / "wallpaper.jpg")  # no raise

    def test_logs_warning_when_subprocess_raises(self, tmp_path):
        platform = klink.KDEPlatform()
        with patch("shutil.which", return_value="/usr/bin/kwriteconfig6"), \
             patch("subprocess.run", side_effect=OSError("no kwriteconfig")):
            platform.apply_lock_screen(tmp_path / "wallpaper.jpg")  # no raise
