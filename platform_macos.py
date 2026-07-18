# platform_macos.py -- macOS backend for platform_base.WallpaperPlatform
# Copyright (C) 2026 John-Schreiber
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""macOS backend for platform_base.WallpaperPlatform.

Like platform_windows.py and platform_linux_kde.py, this module was originally
written from Apple's documented APIs (`NSWorkspace`/`NSScreen` in the AppKit
framework, reached via the `pyobjc` Python/Objective-C bridge) and known community
recipes -- most notably the wallpaper-scaling-option mapping, which mirrors the
community-verified recipe used by the `desktoppr` command-line tool
(https://github.com/scriptingosx/desktoppr, a widely-used macOS-admin utility for
exactly this job) -- rather than against real hardware. The default single-screen
path (get_screen_size + apply_wallpaper) has since been confirmed on a real
MacBook with a single (built-in) display. list_monitors/apply_wallpaper_per_monitor
against real multi-monitor geometry, and get_power_state's `pmset -g batt` parsing
on battery, are still only exercised through this module's unit tests' mocked
output, not live hardware -- see NEXT_STEPS.md item 22 for the exact breakdown.
Treat those untested paths as "should work per Apple's own docs/a known-good
community tool" rather than "confirmed against real hardware" until someone
exercises them live, same honesty standard platform_linux_kde.py's docstring set
for itself before its own single-screen path got confirmed live.

Wallpaper application and screen geometry go through AppKit's `NSWorkspace`/
`NSScreen` -- the OS's actual supported API for this, not a shell-out hack (like
Windows' COM `IDesktopWallpaper` interface, not like KDE's D-Bus scripting, which
was Plasma's closest equivalent to a "real API" available from Python without a
compiled extension). Battery status goes through the `pmset` command-line tool
instead (mirrors platform_linux_kde.py's `upower`/`nmcli` subprocess-parsing
approach) -- pyobjc doesn't provide power-management framework bindings out of the
box, and `pmset -g batt`'s text output is stable, documented, and simple enough to
parse reliably.

Coordinate system note (read this before touching list_monitors/get_screen_size):
AppKit's NSScreen.frame() uses Cocoa's coordinate system, which is bottom-up (y
increases *upward*, origin at the primary screen's bottom-left corner), with every
screen's frame expressed in one shared virtual-desktop space. Both other backends'
geometry (Windows' GetMonitorRECT, KDE's screenGeometry()) -- and this project's own
MonitorInfo.top/bottom -- are top-down (y increases *downward*, origin at the
top-left), matching how every image library (Pillow included) and every other OS
lays out pixels. list_monitors() below flips Cocoa's y-axis before returning
MonitorInfo objects for exactly this reason: skip the flip and per-monitor
wallpaper placement would come out vertically mirrored relative to the other two
backends' notion of "top" and "bottom".

Known limitation: "tile" and "span" (WALLPAPER_STYLE_NAMES) have no NSWorkspace
equivalent. "tile" was removed from System Settings' own wallpaper UI in recent
macOS versions (no NSImageScaling value repeats/tiles a sub-native-resolution
image); "span" (one image spanning every monitor's shared coordinate space) has no
API at all -- NSWorkspace's desktop-image API is inherently per-NSScreen. Both
degrade to "fill" with a logged warning, the same pattern platform_linux_kde.py
uses to degrade "span" to "fill" (see that module's docstring/apply_wallpaper).

Known limitation (is_network_metered): macOS has no reliable CLI or public
framework API for querying a network's "Low Data Mode"/metered status
per-interface. Always returns None -- see is_network_metered's own docstring.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import AppKit
import Foundation

from platform_base import MonitorInfo, PowerState, WallpaperPlatform

# Used only if NSScreen.screens() somehow comes back empty (should not happen on a
# real logged-in macOS session, but mirrors the KDE backend's own fallback for
# when its equivalent detection is unreachable). A common baseline resolution, not
# detected from real hardware.
_FALLBACK_SIZE = (1920, 1080)

_PMSET_TIMEOUT = 5

# WALLPAPER_STYLE_NAMES -> (NSImageScaling value, NSWorkspaceDesktopImageAllowClippingKey).
# Matches the community-verified recipe used by desktoppr
# (https://github.com/scriptingosx/desktoppr, see its source for the same mapping)
# rather than being re-derived from scratch here. "tile" and "span" have no
# NSWorkspace equivalent (see module docstring) and are mapped to the same values
# as "fill".
_STYLE_OPTIONS = {
    "fill": (AppKit.NSImageScaleProportionallyUpOrDown, True),
    "fit": (AppKit.NSImageScaleProportionallyUpOrDown, False),
    "stretch": (AppKit.NSImageScaleAxesIndependently, True),
    "center": (AppKit.NSImageScaleNone, False),
    "tile": (AppKit.NSImageScaleProportionallyUpOrDown, True),
    "span": (AppKit.NSImageScaleProportionallyUpOrDown, True),
}


def _resolve_style(style: str) -> tuple[int, bool]:
    if style in ("tile", "span"):
        logging.warning(
            "macOS has no NSWorkspace equivalent for wallpaper_style=%r "
            "('tile' was removed from System Settings' own UI in recent macOS; "
            "'span' has no API at all -- desktop image options are inherently "
            "per-NSScreen); falling back to 'fill' (cover-crop) instead.",
            style,
        )
    scaling, clipping = _STYLE_OPTIONS.get(style, _STYLE_OPTIONS["fill"])
    return scaling, clipping


def _screens_vertical_extent(screens) -> tuple[float, float]:
    """min_y/max_y across every NSScreen's frame, in Cocoa's bottom-up virtual-
    desktop space -- the reference points list_monitors()/get_screen_size() need
    to flip that space into the project's top-down MonitorInfo convention (see
    module docstring)."""
    min_y = min(s.frame().origin.y for s in screens)
    max_y = max(s.frame().origin.y + s.frame().size.height for s in screens)
    return min_y, max_y


class MacOSPlatform(WallpaperPlatform):
    def get_screen_size(
        self,
        span_all_monitors: bool,
        width_override: int | None,
        height_override: int | None,
        use_fallback_detection: bool = True,
    ) -> tuple[int, int]:
        if width_override and height_override:
            return width_override, height_override

        screens = AppKit.NSScreen.screens()
        if not screens:
            logging.warning(
                "NSScreen.screens() returned no displays; using fallback size %s. "
                "If this wallpaper looks wrong, set screen_width/screen_height "
                "explicitly in config.toml.",
                _FALLBACK_SIZE,
            )
            return _FALLBACK_SIZE

        if span_all_monitors:
            # Flipping the y-axis doesn't matter for a width/height delta (only for
            # absolute position), so plain min/max over x and the vertical extent
            # helper both work here.
            min_x = min(s.frame().origin.x for s in screens)
            max_x = max(s.frame().origin.x + s.frame().size.width for s in screens)
            min_y, max_y = _screens_vertical_extent(screens)
            return int(max_x - min_x), int(max_y - min_y)

        # mainScreen() is the screen with the key window/menu bar -- the correct
        # "primary display" notion on macOS (not necessarily screens()[0]).
        main = AppKit.NSScreen.mainScreen()
        size = main.frame().size
        return int(size.width), int(size.height)

    def get_taskbar_height(self) -> int:
        main = AppKit.NSScreen.mainScreen()
        if main is None:
            return 0
        # visibleFrame is `frame` inset by the menu bar and Dock. Since Cocoa's
        # origin is bottom-left, visibleFrame.origin.y is exactly the space
        # reserved at the *bottom* of the screen -- the Dock's height when the
        # Dock is docked at the bottom (0 if it's docked left/right instead, or
        # auto-hidden and not currently showing), which matches this method's
        # "currently reserved... at the bottom" contract precisely.
        return int(main.visibleFrame().origin.y)

    def apply_wallpaper(self, path: Path, style: str) -> None:
        scaling, clipping = _resolve_style(style)
        url = Foundation.NSURL.fileURLWithPath_(str(path))
        options = {
            AppKit.NSWorkspaceDesktopImageScalingKey: scaling,
            AppKit.NSWorkspaceDesktopImageAllowClippingKey: clipping,
        }
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        for screen in AppKit.NSScreen.screens():
            ok, error = workspace.setDesktopImageURL_forScreen_options_error_(
                url, screen, options, None
            )
            if not ok:
                logging.warning(
                    "Failed to set desktop wallpaper for a screen: %s", error
                )

    def list_monitors(self) -> list[MonitorInfo]:
        screens = AppKit.NSScreen.screens()
        if not screens:
            return []
        min_y, max_y = _screens_vertical_extent(screens)
        monitors = []
        for screen in screens:
            frame = screen.frame()
            left = frame.origin.x
            right = left + frame.size.width
            # Flip Cocoa's bottom-up y into this project's top-down convention --
            # see module docstring for why.
            top = max_y - (frame.origin.y + frame.size.height)
            bottom = max_y - frame.origin.y
            screen_number = screen.deviceDescription()["NSScreenNumber"]
            monitors.append(
                MonitorInfo(str(int(screen_number)), int(left), int(top), int(right), int(bottom))
            )
        return monitors

    def apply_wallpaper_per_monitor(self, assignments: dict[str, Path], style: str) -> None:
        if not assignments:
            return
        scaling, clipping = _resolve_style(style)
        options = {
            AppKit.NSWorkspaceDesktopImageScalingKey: scaling,
            AppKit.NSWorkspaceDesktopImageAllowClippingKey: clipping,
        }
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        screens_by_id = {
            str(int(screen.deviceDescription()["NSScreenNumber"])): screen
            for screen in AppKit.NSScreen.screens()
        }
        for mon_id, path in assignments.items():
            screen = screens_by_id.get(mon_id)
            if screen is None:
                logging.warning(
                    "Skipping wallpaper assignment for monitor id %r: not a "
                    "currently active screen.", mon_id,
                )
                continue
            url = Foundation.NSURL.fileURLWithPath_(str(path))
            ok, error = workspace.setDesktopImageURL_forScreen_options_error_(
                url, screen, options, None
            )
            if not ok:
                logging.warning(
                    "Failed to set desktop wallpaper for monitor id %r: %s", mon_id, error
                )

    def get_power_state(self) -> PowerState:
        pmset = shutil.which("pmset")
        if not pmset:
            return PowerState(on_battery=None)
        try:
            proc = subprocess.run(
                [pmset, "-g", "batt"],
                capture_output=True, text=True, timeout=_PMSET_TIMEOUT,
            )
        except (subprocess.SubprocessError, OSError):
            return PowerState(on_battery=None)
        if proc.returncode != 0 or not proc.stdout.strip():
            return PowerState(on_battery=None)

        lines = proc.stdout.splitlines()
        first_line = lines[0] if lines else ""
        if "Battery Power" not in first_line and "AC Power" not in first_line:
            return PowerState(on_battery=None)

        # Desktop Macs with no battery report "AC Power" with no "InternalBattery"
        # line at all -- that's a known/expected state, not an undetectable one.
        battery_line = next((l for l in lines if "InternalBattery" in l), None)
        if battery_line is None:
            return PowerState(on_battery=False, battery_percent=None)

        percent = None
        idx = battery_line.find("%")
        if idx != -1:
            start = idx
            while start > 0 and (battery_line[start - 1].isdigit() or battery_line[start - 1] == "."):
                start -= 1
            try:
                percent = float(battery_line[start:idx])
            except ValueError:
                percent = None

        # "Battery Power" in the first line is a simpler/more robust on-battery
        # signal than parsing the per-battery state word (charging/discharging/
        # charged/finishing charge/...), since it reflects the actual power
        # source rather than needing to enumerate every state word.
        on_battery = "Battery Power" in first_line
        return PowerState(on_battery=on_battery, battery_percent=percent)

    def is_network_metered(self) -> bool | None:
        # macOS has no reliable CLI or public framework API for querying a
        # network's "Low Data Mode"/metered status per-interface -- unlike
        # Windows' winrt NetworkCostType or Linux's nmcli GENERAL.METERED, there
        # is nothing analogous to shell out to or bind against here.
        return None

    def default_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "GOES-Wallpaper"

    def default_font_path(self) -> str:
        # Arial has shipped as a supplemental system font on macOS for a very long
        # time. This is *not* a live-verified path (unlike KDE's font path, which
        # was confirmed present at this exact location on that dev's real Plasma
        # install via `fc-match`) -- it's based on Arial's long-standing presence
        # as an Apple-shipped supplemental font, not a check against a real
        # machine. Callers already fall back to Pillow's built-in font if this
        # path doesn't exist (see WallpaperPlatform.default_font_path's
        # docstring), so a macOS version that's moved/removed it degrades
        # gracefully rather than breaking the info bar.
        return "/System/Library/Fonts/Supplemental/Arial.ttf"
