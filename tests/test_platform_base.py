# tests/test_platform_base.py -- platform_base.get_platform()'s selection logic
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
#
# platform_windows.py needs comtypes/winrt-*, which are only installed on Windows
# (see pyproject.toml's sys_platform == 'win32' markers) -- so real "windows"
# override coverage is skipped on other platforms, matching CI's approach for the
# rest of the suite (platform-dependent logic tested via a stub, real backends only
# exercised on their own OS). platform_linux_kde.py and platform_render.py have no
# OS-locked imports (plain subprocess/shutil/json, or os/pathlib respectively), so
# their override paths are exercised for real everywhere.

import sys

import pytest

import platform_base


class TestGetPlatformOverride:
    def test_kde_override_returns_kde_platform_regardless_of_os(self):
        from platform_linux_kde import KDEPlatform

        assert isinstance(platform_base.get_platform("kde"), KDEPlatform)

    def test_kde_override_bypasses_environment_sniffing(self, monkeypatch):
        # No XDG_CURRENT_DESKTOP/XDG_SESSION_DESKTOP at all -- "auto" detection
        # would fail here, but the explicit override should still win.
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
        from platform_linux_kde import KDEPlatform

        assert isinstance(platform_base.get_platform("kde"), KDEPlatform)

    @pytest.mark.skipif(sys.platform != "win32", reason="platform_windows needs Windows-only comtypes/winrt deps")
    def test_windows_override_returns_windows_platform(self):
        from platform_windows import WindowsPlatform

        assert isinstance(platform_base.get_platform("windows"), WindowsPlatform)

    def test_render_override_returns_render_only_platform(self):
        from platform_render import RenderOnlyPlatform

        assert isinstance(platform_base.get_platform("render"), RenderOnlyPlatform)

    def test_render_override_bypasses_environment_sniffing(self, monkeypatch):
        # Same "would fail 'auto' detection, but the explicit override still wins"
        # shape as the KDE override tests above -- "render" doesn't even try to
        # detect anything, so this mostly documents that it's unaffected by env.
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
        from platform_render import RenderOnlyPlatform

        assert isinstance(platform_base.get_platform("render"), RenderOnlyPlatform)

    def test_render_override_forwards_fallback_size(self):
        platform = platform_base.get_platform(
            "render", render_fallback_width=3840, render_fallback_height=2160,
        )
        assert platform.get_screen_size(False, None, None) == (3840, 2160)

    def test_render_fallback_size_ignored_by_other_backends(self, monkeypatch):
        # render_fallback_width/height only mean anything to RenderOnlyPlatform --
        # confirm passing them alongside "kde" doesn't raise (KDEPlatform() takes no
        # constructor arguments at all).
        from platform_linux_kde import KDEPlatform

        platform = platform_base.get_platform(
            "kde", render_fallback_width=3840, render_fallback_height=2160,
        )
        assert isinstance(platform, KDEPlatform)


class TestGetPlatformAutoDetection:
    def test_auto_detects_kde_from_xdg_current_desktop(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
        from platform_linux_kde import KDEPlatform

        assert isinstance(platform_base.get_platform("auto"), KDEPlatform)

    def test_auto_detects_kde_case_insensitively_from_xdg_session_desktop(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "kde-plasma")
        from platform_linux_kde import KDEPlatform

        assert isinstance(platform_base.get_platform("auto"), KDEPlatform)

    def test_auto_raises_for_unrecognized_linux_desktop(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
        with pytest.raises(NotImplementedError, match="No WallpaperPlatform backend"):
            platform_base.get_platform("auto")

    def test_auto_raises_for_unsupported_sys_platform(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(NotImplementedError, match="No WallpaperPlatform backend"):
            platform_base.get_platform("auto")

    def test_default_argument_is_auto(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
        from platform_linux_kde import KDEPlatform

        assert isinstance(platform_base.get_platform(), KDEPlatform)
