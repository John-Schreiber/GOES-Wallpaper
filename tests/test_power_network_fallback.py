# tests/test_power_network_fallback.py -- should_skip_for_power, maybe_apply_metered_resolution
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from platform_base import MonitorInfo, PowerState, WallpaperPlatform

import goes_wallpaper as gw


class FakePlatform(WallpaperPlatform):
    """Minimal stub implementing every WallpaperPlatform method, so tests never touch
    real OS APIs (mirrors the pattern used to verify this logic live during
    development, before wiring it into goes_wallpaper.py)."""

    def __init__(self, on_battery=False, battery_percent=None, metered=False):
        self._power = PowerState(on_battery=on_battery, battery_percent=battery_percent)
        self._metered = metered

    def get_screen_size(self, span_all_monitors, width_override, height_override, use_fallback_detection=True):
        return (1920, 1080)

    def get_taskbar_height(self):
        return 0

    def apply_wallpaper(self, path, style):
        pass

    def list_monitors(self):
        return [MonitorInfo("m0", 0, 0, 1920, 1080)]

    def apply_wallpaper_per_monitor(self, assignments, style):
        pass

    def get_power_state(self):
        return self._power

    def is_network_metered(self):
        return self._metered

    def default_data_dir(self):
        from pathlib import Path
        return Path("/fake-data-dir")

    def default_font_path(self):
        return "/fake/font.ttf"


class TestShouldSkipForPower:
    def test_disabled_never_skips(self):
        cfg = gw.Config(skip_on_battery=False)
        assert gw.should_skip_for_power(cfg, FakePlatform(on_battery=True)) is False

    def test_enabled_and_on_battery_skips(self):
        cfg = gw.Config(skip_on_battery=True)
        assert gw.should_skip_for_power(cfg, FakePlatform(on_battery=True)) is True

    def test_enabled_and_on_ac_does_not_skip(self):
        cfg = gw.Config(skip_on_battery=True)
        assert gw.should_skip_for_power(cfg, FakePlatform(on_battery=False)) is False

    def test_unknown_battery_state_never_skips(self):
        cfg = gw.Config(skip_on_battery=True)
        assert gw.should_skip_for_power(cfg, FakePlatform(on_battery=None)) is False


class TestMaybeApplyMeteredResolution:
    def test_disabled_returns_original_source(self):
        cfg = gw.Config(satellite="GOES18", metered_resolution=None)
        source = gw.resolve_source(cfg, None)
        result = gw.maybe_apply_metered_resolution(cfg, source, FakePlatform(metered=True))
        assert result.resolution == source.resolution

    def test_enabled_and_metered_overrides_resolution(self):
        cfg = gw.Config(satellite="GOES18", resolution="5000x3000", metered_resolution="1250x750")
        source = gw.resolve_source(cfg, None)
        result = gw.maybe_apply_metered_resolution(cfg, source, FakePlatform(metered=True))
        assert result.resolution == "1250x750"
        # everything else about the source is untouched
        assert result.satellite == source.satellite
        assert result.name == source.name

    def test_enabled_and_not_metered_keeps_original(self):
        cfg = gw.Config(satellite="GOES18", resolution="5000x3000", metered_resolution="1250x750")
        source = gw.resolve_source(cfg, None)
        result = gw.maybe_apply_metered_resolution(cfg, source, FakePlatform(metered=False))
        assert result.resolution == "5000x3000"

    def test_unknown_metered_state_keeps_original(self):
        cfg = gw.Config(satellite="GOES18", resolution="5000x3000", metered_resolution="1250x750")
        source = gw.resolve_source(cfg, None)
        result = gw.maybe_apply_metered_resolution(cfg, source, FakePlatform(metered=None))
        assert result.resolution == "5000x3000"

    def test_override_equal_to_current_is_a_no_op(self):
        cfg = gw.Config(satellite="GOES18", resolution="5000x3000", metered_resolution="5000x3000")
        source = gw.resolve_source(cfg, None)
        result = gw.maybe_apply_metered_resolution(cfg, source, FakePlatform(metered=True))
        assert result is source  # early-return identity
