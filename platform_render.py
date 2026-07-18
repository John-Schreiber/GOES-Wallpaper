# platform_render.py -- render-only backend for platform_base.WallpaperPlatform
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

"""Render-only backend for platform_base.WallpaperPlatform -- for headless boxes
with no desktop shell at all (a server, a container, an SSH session, CI) where
the point is just to produce the rendered image(s) via `render_to`/`data_dir`,
never to set a desktop wallpaper. Unlike platform_windows.py/platform_linux_kde.py,
this isn't a real-hardware-validated OS integration -- there's no hardware to
validate against, by design. Every method either returns a fixed, documented
fallback or is a deliberate no-op; none of it does OS detection or talks to a
desktop shell.

Never auto-selected by get_platform("auto") -- a headless/unsupported desktop
environment should still raise NotImplementedError there (see platform_base.py)
so a user on a real desktop finds out their DE isn't supported yet, rather than
silently getting a backend that never applies the wallpaper. Set
`platform = "render"` in config.toml explicitly to opt in, typically alongside
`render_to` (goes_wallpaper.py's Config.render_to already skips wallpaper
application when set -- this backend also no-ops apply_wallpaper*  on its own,
so the same effect holds even if render_to is left unset).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from platform_base import MonitorInfo, PowerState, WallpaperPlatform

# Used for get_screen_size/list_monitors whenever width/height aren't otherwise
# known -- a common baseline resolution, not detected from real hardware (there's
# no display to detect on a headless box). Matches platform_linux_kde.py's
# _FALLBACK_SIZE for the same reason: a sane default beats refusing to render.
_FALLBACK_SIZE = (1920, 1080)

_SYNTHETIC_MONITOR_ID = "0"


class RenderOnlyPlatform(WallpaperPlatform):
    """`fallback_width`/`fallback_height` (both required together, like
    get_screen_size's own width_override/height_override) size the synthetic
    "display" this backend renders against when nothing else says otherwise --
    _FALLBACK_SIZE if left unset. They exist specifically for list_monitors(),
    which -- unlike get_screen_size() -- takes no per-call size arguments at all
    (it's a fixed WallpaperPlatform abstract method signature shared with the
    real hardware-detecting backends), so this is the only place combo_mode =
    "per_monitor" can be sized on a backend with no real monitor to detect.
    get_platform() forwards config.toml's screen_width/screen_height here for the
    "render" override -- see its docstring."""

    def __init__(self, fallback_width: int | None = None, fallback_height: int | None = None):
        if fallback_width and fallback_height:
            self._fallback_size = (fallback_width, fallback_height)
        else:
            self._fallback_size = _FALLBACK_SIZE

    def get_screen_size(
        self,
        span_all_monitors: bool,
        width_override: int | None,
        height_override: int | None,
        use_fallback_detection: bool = True,
    ) -> tuple[int, int]:
        if width_override and height_override:
            return width_override, height_override
        logging.info(
            "Render-only backend has no display to detect; using %s. Set "
            "screen_width/screen_height in config.toml for a different render size.",
            self._fallback_size,
        )
        return self._fallback_size

    def get_taskbar_height(self) -> int:
        return 0

    def apply_wallpaper(self, path: Path, style: str) -> None:
        logging.info(
            "Render-only backend: not applying %s as a desktop wallpaper "
            "(no desktop shell to apply it to).", path,
        )

    def list_monitors(self) -> list[MonitorInfo]:
        width, height = self._fallback_size
        return [MonitorInfo(_SYNTHETIC_MONITOR_ID, 0, 0, width, height)]

    def apply_wallpaper_per_monitor(self, assignments: dict[str, Path], style: str) -> None:
        for mon_id, path in assignments.items():
            logging.info(
                "Render-only backend: not applying %s to monitor %s "
                "(no desktop shell to apply it to).", path, mon_id,
            )

    def get_power_state(self) -> PowerState:
        return PowerState(on_battery=None)

    def is_network_metered(self) -> bool | None:
        return None

    def default_data_dir(self) -> Path:
        # XDG_DATA_HOME/~/.local/share is the same convention platform_linux_kde.py
        # uses -- reused here rather than inventing a third scheme, since this
        # backend is OS-agnostic by design (it's opted into explicitly, not tied to
        # any one OS's directory conventions) and XDG_DATA_HOME resolves to a
        # sensible subdirectory of $HOME even on platforms that don't formally
        # follow the XDG spec.
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
        return base / "goes-wallpaper"

    def default_font_path(self) -> str:
        # A guess, not a detection -- DejaVu Sans ships as a base font package
        # (fonts-dejavu-core) on most Debian/Ubuntu images, including minimal
        # container images this backend is likely to run in. Callers already fall
        # back to Pillow's built-in default font if this path doesn't exist (see
        # WallpaperPlatform.default_font_path's docstring), so a runner without it
        # degrades gracefully rather than breaking the info bar.
        return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
