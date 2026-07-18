# tests/test_platform_render.py -- RenderOnlyPlatform's fixed-fallback/no-op behavior
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""platform_render.py has no OS integration to mock -- every method is a fixed
fallback or a deliberate no-op, so these tests just confirm that contract holds
(no exceptions, no attempt to touch a real desktop shell) rather than exercising
any subprocess/D-Bus/registry behavior like the Windows/KDE backend tests do."""

from pathlib import Path

from platform_base import PowerState
from platform_render import RenderOnlyPlatform, _FALLBACK_SIZE


class TestGetScreenSize:
    def test_uses_explicit_overrides(self):
        platform = RenderOnlyPlatform()
        assert platform.get_screen_size(False, 800, 600) == (800, 600)

    def test_falls_back_to_fixed_size_without_overrides(self):
        platform = RenderOnlyPlatform()
        assert platform.get_screen_size(False, None, None) == _FALLBACK_SIZE

    def test_falls_back_when_only_one_override_given(self):
        platform = RenderOnlyPlatform()
        assert platform.get_screen_size(False, 800, None) == _FALLBACK_SIZE

    def test_span_all_monitors_does_not_change_fallback(self):
        platform = RenderOnlyPlatform()
        assert platform.get_screen_size(True, None, None) == _FALLBACK_SIZE


class TestNoDesktopShell:
    def test_taskbar_height_is_always_zero(self):
        assert RenderOnlyPlatform().get_taskbar_height() == 0

    def test_apply_wallpaper_does_not_raise(self):
        RenderOnlyPlatform().apply_wallpaper(Path("/tmp/wallpaper.jpg"), "fill")

    def test_apply_wallpaper_per_monitor_does_not_raise(self):
        RenderOnlyPlatform().apply_wallpaper_per_monitor(
            {"0": Path("/tmp/wallpaper_monitor0.jpg")}, "fill"
        )

    def test_apply_wallpaper_per_monitor_handles_empty_assignments(self):
        RenderOnlyPlatform().apply_wallpaper_per_monitor({}, "fill")


class TestListMonitors:
    def test_returns_one_synthetic_monitor_matching_fallback_size(self):
        monitors = RenderOnlyPlatform().list_monitors()
        assert len(monitors) == 1
        width, height = _FALLBACK_SIZE
        assert monitors[0].width == width
        assert monitors[0].height == height


class TestPowerNetwork:
    def test_power_state_is_undetectable(self):
        assert RenderOnlyPlatform().get_power_state() == PowerState(on_battery=None)

    def test_network_metered_is_undetectable(self):
        assert RenderOnlyPlatform().is_network_metered() is None


class TestDefaultPaths:
    def test_default_data_dir_uses_xdg_data_home_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert RenderOnlyPlatform().default_data_dir() == tmp_path / "goes-wallpaper"

    def test_default_data_dir_falls_back_to_home_local_share(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert RenderOnlyPlatform().default_data_dir() == Path.home() / ".local" / "share" / "goes-wallpaper"

    def test_default_font_path_is_nonempty(self):
        assert RenderOnlyPlatform().default_font_path()
