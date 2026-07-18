# tests/test_platform_windows.py -- WindowsPlatform.get_taskbar_height
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit-tests get_taskbar_height's SHAppBarMessage-based edge detection: ctypes.windll
is mocked throughout, so these confirm the Python-side logic (only a bottom-docked
taskbar contributes a margin), not the real Shell_TrayWnd/SHAppBarMessage behavior
(verified against real hardware -- see platform_windows.py's module docstring).

Windows-only: unlike platform_linux_kde.py (subprocess-based, no OS-specific import at
module level), platform_windows.py imports comtypes/winrt unconditionally, which
aren't installed on other platforms at all (see pyproject.toml's sys_platform ==
'win32' markers) -- so this module must skip at collection time on Linux/macOS CI
rather than fail to import."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only: platform_windows.py imports comtypes/winrt", allow_module_level=True)

import ctypes
from unittest.mock import patch

import platform_windows as pw


def _appbar_reply(edge, top, bottom):
    """Build a fake SHAppBarMessage(ABM_GETTASKBARPOS) implementation that writes
    the given edge/rect into the caller's APPBARDATA and reports success."""
    def fake(msg, data_ref):
        assert msg == pw._ABM_GETTASKBARPOS
        data = ctypes.cast(data_ref, ctypes.POINTER(pw._APPBARDATA)).contents
        data.uEdge = edge
        data.rc.left = 0
        data.rc.top = top
        data.rc.right = 1920
        data.rc.bottom = bottom
        return 1
    return fake


@pytest.fixture
def platform():
    return pw.WindowsPlatform()


class TestGetTaskbarHeight:
    def test_no_taskbar_window_found_returns_zero(self, platform):
        with patch.object(ctypes.windll.user32, "FindWindowW", lambda *a: 0):
            assert platform.get_taskbar_height() == 0

    def test_bottom_docked_taskbar_returns_its_height(self, platform):
        with patch.object(ctypes.windll.user32, "FindWindowW", lambda *a: 12345):
            with patch.object(ctypes.windll.shell32, "SHAppBarMessage", _appbar_reply(pw._ABE_BOTTOM, 1040, 1080)):
                assert platform.get_taskbar_height() == 40

    def test_left_docked_taskbar_contributes_no_bottom_margin(self, platform):
        # A left/right-docked taskbar's rect spans the full screen height -- reading
        # that as a bottom margin would push the info bar almost entirely off-image.
        with patch.object(ctypes.windll.user32, "FindWindowW", lambda *a: 12345):
            with patch.object(ctypes.windll.shell32, "SHAppBarMessage", _appbar_reply(pw._ABE_LEFT, 0, 1080)):
                assert platform.get_taskbar_height() == 0

    def test_top_docked_taskbar_contributes_no_bottom_margin(self, platform):
        with patch.object(ctypes.windll.user32, "FindWindowW", lambda *a: 12345):
            with patch.object(ctypes.windll.shell32, "SHAppBarMessage", _appbar_reply(pw._ABE_TOP, 0, 40)):
                assert platform.get_taskbar_height() == 0

    def test_right_docked_taskbar_contributes_no_bottom_margin(self, platform):
        with patch.object(ctypes.windll.user32, "FindWindowW", lambda *a: 12345):
            with patch.object(ctypes.windll.shell32, "SHAppBarMessage", _appbar_reply(pw._ABE_RIGHT, 0, 1080)):
                assert platform.get_taskbar_height() == 0

    def test_sh_app_bar_message_failure_returns_zero(self, platform):
        with patch.object(ctypes.windll.user32, "FindWindowW", lambda *a: 12345):
            with patch.object(ctypes.windll.shell32, "SHAppBarMessage", lambda *a: 0):
                assert platform.get_taskbar_height() == 0
