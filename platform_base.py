# platform_base.py -- per-OS backend interface for goes_wallpaper.py
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

"""Per-OS backend interface for goes_wallpaper.py's platform-specific operations:
applying the wallpaper, screen/monitor geometry, taskbar/dock avoidance, and
power/network state for the fallback logic in the main script.

To port to a new OS: implement WallpaperPlatform below (platform_windows.py and
platform_linux_kde.py are the reference implementations — every method in each was
validated against real hardware, see their own docstrings) in a new
platform_<name>.py, and add a branch in get_platform(). A backend for any other OS or
desktop environment (GNOME, etc.) is welcome — none prioritized over another.

Note: this module (and any platform_*.py) must never import from goes_wallpaper.py —
goes_wallpaper.py already imports from here, so the reverse would be circular.
Dependencies only flow one way. That's also why these interfaces take plain
primitives (paths, ints, bools, dicts) rather than the app's Config object — keeps
the platform layer decoupled from the app's config schema.
"""

from __future__ import annotations

import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

# Logical wallpaper style names the CLI/config accept. Each backend maps these to its
# own OS-specific mechanism (Windows: a registry code pair + a COM position enum). A
# future backend may only support a subset — document that in its class docstring.
WALLPAPER_STYLE_NAMES = ("fill", "fit", "stretch", "tile", "center", "span")


@dataclass(slots=True)
class MonitorInfo:
    """One physical monitor, in whatever stable enumeration order the backend
    reports — config.toml's combo.monitor indices refer to this order."""
    id: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(slots=True)
class PowerState:
    on_battery: bool | None  # None = undetectable on this platform/hardware
    battery_percent: float | None = None  # 0-100, None if unknown even when on_battery is known


class WallpaperPlatform(ABC):
    """Everything goes_wallpaper.py needs from the OS beyond fetching/rendering
    images. Every method should degrade gracefully (log and return a safe default)
    rather than raising, since this runs unattended (--loop or a scheduled task)."""

    @abstractmethod
    def get_screen_size(
        self,
        span_all_monitors: bool,
        width_override: int | None,
        height_override: int | None,
        use_fallback_detection: bool = True,
    ) -> tuple[int, int]:
        """Primary display size, or the full virtual desktop if span_all_monitors.
        width_override/height_override, if both given, short-circuit detection
        entirely (the user knows better than auto-detection in their setup).
        use_fallback_detection controls whether the backend may fall back to a
        secondary detection method if its primary one gives an untrustworthy answer
        (e.g. Windows falls back to a WMI query when GetSystemMetrics reports the
        non-interactive-session default) — backends without such a fallback ignore
        it."""

    @abstractmethod
    def get_taskbar_height(self) -> int:
        """Pixels currently reserved by the taskbar/dock at the bottom of the
        primary display, or 0 if unknown/not applicable/no taskbar concept."""

    @abstractmethod
    def apply_wallpaper(self, path: Path, style: str) -> None:
        """Set path as the wallpaper for the whole desktop, honoring style (one of
        WALLPAPER_STYLE_NAMES)."""

    @abstractmethod
    def list_monitors(self) -> list[MonitorInfo]:
        """One MonitorInfo per currently active physical monitor."""

    @abstractmethod
    def apply_wallpaper_per_monitor(self, assignments: dict[str, Path], style: str) -> None:
        """assignments: MonitorInfo.id -> image path, applied independently so each
        monitor can show different content (unlike apply_wallpaper, which is one
        image for the whole desktop)."""

    @abstractmethod
    def get_power_state(self) -> PowerState:
        """Best-effort battery/AC status, for power-aware fallbacks."""

    @abstractmethod
    def is_network_metered(self) -> bool | None:
        """Best-effort: is the current network connection cost-metered (e.g.
        cellular/tethered)? None if undetectable on this platform."""

    def supports_lock_screen(self) -> bool:
        """Whether apply_lock_screen() does anything real on this backend. False by
        default -- concrete (not abstract) so most backends need no override at all.
        Currently True only for WindowsPlatform (see its module docstring for what
        was actually verified); no macOS/KDE equivalent has been investigated yet.
        goes_wallpaper.py's validate_lock_screen() checks this at startup so
        set_lock_screen = true fails fast on an unsupported backend instead of
        silently no-op-ing every cycle."""
        return False

    def apply_lock_screen(self, path: Path) -> None:
        """Set path as the lock screen image. Only called when supports_lock_screen()
        is True (validate_lock_screen() enforces this before any cycle runs), but
        still degrades gracefully -- log and return, don't raise -- like every other
        method here, in case a backend's underlying API stops working after startup
        (e.g. an OS update, a permissions change)."""
        logging.warning("Lock screen image not supported on this backend; ignoring.")

    @abstractmethod
    def default_data_dir(self) -> Path:
        """Where to store the wallpaper/state/logs when config.toml/--data-dir
        doesn't say otherwise. Per-platform because e.g. Windows' AppData layout
        means nothing on Linux/macOS."""

    @abstractmethod
    def default_font_path(self) -> str:
        """Info-bar/overlay font to use when info_font_path isn't set in config.
        Per-platform since font install locations differ; callers already handle
        this path not existing (falls back to Pillow's built-in default font)."""


def get_platform(
    override: str = "auto",
    *,
    render_fallback_width: int | None = None,
    render_fallback_height: int | None = None,
) -> WallpaperPlatform:
    """Construct the backend for the current OS. `override` is deliberately almost
    the only argument — per-call behavior (like whether to use fallback screen-size
    detection) belongs on the relevant WallpaperPlatform method instead, not baked
    into construction, so this factory doesn't accumulate one backend's config
    knobs. `override` is an exception to that: it picks *which* backend, not how one
    behaves, so it stays here rather than on any individual backend.

    `render_fallback_width`/`render_fallback_height` are a second, narrower
    exception, forwarded only to platform_render.RenderOnlyPlatform (ignored for
    every other backend, which detects real hardware and has no use for a size
    hint at construction time). They exist because RenderOnlyPlatform.list_monitors()
    -- unlike get_screen_size() -- has no per-call size parameters at all (a fixed
    WallpaperPlatform signature shared with the real backends), so a
    combo_mode = "per_monitor" render size can only reach it via construction.
    goes_wallpaper.main() passes Config.screen_width/screen_height here.

    "auto" (default) preserves today's sys.platform/XDG_CURRENT_DESKTOP sniffing
    below. An explicit "windows"/"kde"/"macos" short-circuits that sniffing entirely --
    for forcing a backend under a session where env-var detection is unreliable, or
    for testing. "render" is a third explicit option: platform_render.
    RenderOnlyPlatform, which never applies a desktop wallpaper (see its module
    docstring) -- for headless boxes with no desktop shell at all, typically paired
    with Config.render_to. Unlike "windows"/"kde", "render" is never chosen by
    "auto" detection below, even on an unrecognized OS/desktop -- it's opt-in only,
    so an unsupported real desktop still raises rather than silently doing nothing.
    Config.platform (goes_wallpaper.py) is validated against the same set of names
    by validate_platform() before this is called, so an unrecognized value never
    reaches here."""
    if override == "windows":
        from platform_windows import WindowsPlatform
        return WindowsPlatform()
    if override == "kde":
        from platform_linux_kde import KDEPlatform
        return KDEPlatform()
    if override == "macos":
        from platform_macos import MacOSPlatform
        return MacOSPlatform()
    if override == "render":
        from platform_render import RenderOnlyPlatform
        return RenderOnlyPlatform(render_fallback_width, render_fallback_height)

    if sys.platform == "win32":
        from platform_windows import WindowsPlatform
        return WindowsPlatform()
    if sys.platform == "darwin":
        from platform_macos import MacOSPlatform
        return MacOSPlatform()
    if sys.platform.startswith("linux"):
        desktop = (
            os.environ.get("XDG_CURRENT_DESKTOP", "")
            + os.environ.get("XDG_SESSION_DESKTOP", "")
        ).lower()
        if "kde" in desktop:
            from platform_linux_kde import KDEPlatform
            return KDEPlatform()
        raise NotImplementedError(
            f"No WallpaperPlatform backend for this desktop environment "
            f"(XDG_CURRENT_DESKTOP={os.environ.get('XDG_CURRENT_DESKTOP')!r}) yet.\n"
            "Only KDE Plasma (platform_linux_kde.KDEPlatform) is implemented so far "
            "on Linux. To add another: implement platform_base.WallpaperPlatform in "
            "a new platform_<name>.py and add a branch here. Or, if this is actually "
            "a KDE session that XDG_CURRENT_DESKTOP/XDG_SESSION_DESKTOP just didn't "
            "identify correctly, set `platform = \"kde\"` in config.toml to force it. "
            "If there's no desktop shell at all (a headless box/container/CI) and "
            "you just want the rendered image(s), set `platform = \"render\"` "
            "instead -- see platform_render.RenderOnlyPlatform."
        )
    raise NotImplementedError(
        f"No WallpaperPlatform backend for sys.platform={sys.platform!r} yet.\n"
        "To add one: implement platform_base.WallpaperPlatform (see "
        "platform_windows.WindowsPlatform or platform_linux_kde.KDEPlatform for "
        "reference implementations) in a new platform_<name>.py, and add a branch "
        "here. If there's no desktop shell at all (a headless box/container/CI) "
        "and you just want the rendered image(s), set `platform = \"render\"` "
        "instead -- see platform_render.RenderOnlyPlatform."
    )
