# platform_linux_kde.py -- KDE Plasma backend for platform_base.WallpaperPlatform
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

"""KDE Plasma backend for platform_base.WallpaperPlatform.

Unlike platform_windows.py, this was built from documentation and working
community examples rather than against a live KDE session (development here is on
Windows) -- see NEXT_STEPS.md for what's still unverified. Treat method bodies as
"should work per KDE's own docs/source" rather than "confirmed against real
hardware" until someone with a KDE box tries it.

Screen geometry, taskbar (panel) height, and both apply_wallpaper paths are done
through Plasma's own D-Bus scripting interface (`qdbus .../PlasmaShell
evaluateScript`, a small JS API Plasma itself exposes for the desktop shell --
see https://develop.kde.org/docs/plasma/scripting/ and
https://develop.kde.org/docs/plasma/scripting/api/) rather than X11-only tools
like xrandr, since Plasma also has to work under Wayland where xrandr doesn't
apply at all. The per-monitor wallpaper JS (setting wallpaperPlugin/writeConfig
per desktop()) mirrors a working, tested snippet from
https://powersnail.com/2023/set-plasma-wallpaper/, which KDE's own docs don't
spell out end-to-end.

apply_wallpaper (the whole-desktop case) prefers the `plasma-apply-wallpaperimage`
CLI (shipped since Plasma 5.24) when present, since it's the officially
supported/stable tool for that one job. It was deliberately *not* used for
per-monitor wallpapers: reading its source
(https://invent.kde.org/plasma/plasma-workspace/-/blob/master/wallpapers/image/plasma-apply-wallpaperimage.cpp)
shows it iterates every desktop() identically -- there is no per-screen targeting,
despite some stale blog posts claiming a `--screen` flag exists.

Known limitation: "span" (one image spanning the full virtual desktop, sliced
per monitor with a shared coordinate space) has no Plasma equivalent -- each
screen's containment crops its own wallpaper independently, there's no
"spanned image" wallpaper plugin in stock Plasma the way Windows'
IDesktopWallpaper has DWPOS_SPAN. apply_wallpaper/apply_wallpaper_per_monitor
degrade "span" to "fill" (cover-crop) with a logged warning rather than
producing a misaligned image.

Known limitation: all of this requires a running `plasmashell` D-Bus session
(DBUS_SESSION_BUS_ADDRESS pointing at the logged-in user's session bus). A
plain cron job or a systemd *system* (not --user) service won't have that --
see https://discuss.kde.org/t/using-plasma-apply-wallpaperimage-in-cron-job/302.
Run this from a systemd --user timer or something else inheriting the desktop
session, the same way platform_windows.py's non-interactive-session caveat
applies to Task Scheduler running with no user logged on.

Power/network detection use `upower`/`nmcli` directly rather than any
KDE-specific API -- both are the de facto standard on Linux desktops generally
(Plasma's own battery/network applets sit on top of the same daemons), not
something specific to Plasma worth re-deriving here.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from platform_base import MonitorInfo, PowerState, WallpaperPlatform

_EVALUATE_SCRIPT_TIMEOUT = 10

# Used only when plasmashell's D-Bus scripting interface is unreachable (no Plasma
# session, or DBUS_SESSION_BUS_ADDRESS unset -- see module docstring). A common
# baseline resolution, not detected from real hardware.
_FALLBACK_SIZE = (1920, 1080)

# style name -> (plasma-apply-wallpaperimage --fill-mode value, org.kde.image's
# "FillMode" config int -- these are Qt's Image.fillMode enum: Stretch=0,
# PreserveAspectFit=1, PreserveAspectCrop=2, Tile=3, TileVertically=4,
# TileHorizontally=5, Pad=6). "span" has no KDE equivalent (see module docstring)
# and is mapped to the same values as "fill".
_FILL_MODES = {
    "fill": ("preserveAspectCrop", 2),
    "fit": ("preserveAspectFit", 1),
    "stretch": ("stretch", 0),
    "tile": ("tile", 3),
    "center": ("pad", 6),
    "span": ("preserveAspectCrop", 2),
}

# Lists every desktop()'s screen geometry as {screen, left, top, width, height}.
# `d.screen != -1` filters out any containment not tied to a physical screen
# (e.g. an activity-only containment); screenGeometry(n)'s left/top/width/height
# properties mirror the pattern used in the powersnail.com reference script.
_SCREEN_GEOMETRY_SCRIPT = (
    "print(JSON.stringify(desktops()"
    ".filter(function(d) { return d.screen != -1; })"
    ".map(function(d) {"
    "  var g = screenGeometry(d.screen);"
    "  return {screen: d.screen, left: g.left, top: g.top, width: g.width, height: g.height};"
    "})));"
)

# Lists every panel as {screen, location, height}; location is one of
# top/bottom/left/right/floating per develop.kde.org's scripting API reference.
_PANEL_SCRIPT = (
    "print(JSON.stringify(panelIds.map(function(id) {"
    "  var p = panelById(id);"
    "  return {screen: p.screen, location: p.location, height: p.height};"
    "})));"
)


@functools.lru_cache(maxsize=1)
def _qdbus_binary() -> str | None:
    """Plasma 6 renamed qdbus to qdbus6 (Qt6-based); Plasma 5 (and some Plasma 6
    distro packages) still ship plain qdbus. Try both, prefer qdbus6."""
    return shutil.which("qdbus6") or shutil.which("qdbus")


class KDEPlatform(WallpaperPlatform):
    def _run_evaluate_script(self, script: str) -> str | None:
        binary = _qdbus_binary()
        if not binary:
            logging.warning(
                "No qdbus6/qdbus binary found; can't talk to Plasma's D-Bus "
                "scripting interface. Is this a KDE Plasma session?"
            )
            return None
        try:
            proc = subprocess.run(
                [binary, "org.kde.plasmashell", "/PlasmaShell",
                 "org.kde.PlasmaShell.evaluateScript", script],
                capture_output=True, text=True, timeout=_EVALUATE_SCRIPT_TIMEOUT,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logging.warning("Failed to run Plasma D-Bus scripting call: %s", exc)
            return None
        if proc.returncode != 0:
            logging.warning(
                "Plasma D-Bus scripting call failed (exit %d): %s",
                proc.returncode, proc.stderr.strip(),
            )
            return None
        return proc.stdout

    def _query_screens(self) -> list[dict] | None:
        out = self._run_evaluate_script(_SCREEN_GEOMETRY_SCRIPT)
        if not out:
            return None
        try:
            screens = json.loads(out)
        except json.JSONDecodeError:
            logging.warning("Could not parse KDE screen geometry output: %r", out)
            return None
        return screens or None

    def get_screen_size(
        self,
        span_all_monitors: bool,
        width_override: int | None,
        height_override: int | None,
        use_fallback_detection: bool = True,
    ) -> tuple[int, int]:
        if width_override and height_override:
            return width_override, height_override

        screens = self._query_screens()
        if not screens:
            logging.warning(
                "Could not detect KDE screen geometry via Plasma's D-Bus scripting "
                "interface; using fallback size %s. If this wallpaper looks wrong, "
                "set screen_width/screen_height explicitly in config.toml, or run "
                "this from a session with a live plasmashell D-Bus connection.",
                _FALLBACK_SIZE,
            )
            return _FALLBACK_SIZE

        if span_all_monitors:
            left = min(s["left"] for s in screens)
            top = min(s["top"] for s in screens)
            right = max(s["left"] + s["width"] for s in screens)
            bottom = max(s["top"] + s["height"] for s in screens)
            return right - left, bottom - top

        # Plasma's scripting API has no explicit "primary screen" accessor; the
        # lowest screen index is used as a best-effort stand-in. Unverified against
        # a multi-monitor KDE setup -- see module docstring.
        primary = min(screens, key=lambda s: s["screen"])
        return primary["width"], primary["height"]

    def get_taskbar_height(self) -> int:
        panels_json = self._run_evaluate_script(_PANEL_SCRIPT)
        if not panels_json:
            return 0
        try:
            panels = json.loads(panels_json)
        except json.JSONDecodeError:
            logging.warning("Could not parse KDE panel list output: %r", panels_json)
            return 0

        screens = self._query_screens()
        primary_screen = min((s["screen"] for s in screens), default=0) if screens else 0

        heights = [
            p["height"] for p in panels
            if p.get("location") == "bottom" and p.get("screen") == primary_screen
        ]
        return max(heights, default=0)

    def apply_wallpaper(self, path: Path, style: str) -> None:
        fill_name, fill_value = _FILL_MODES.get(style, _FILL_MODES["fill"])
        if style == "span":
            logging.warning(
                "KDE has no native multi-monitor wallpaper-spanning primitive; "
                "falling back to 'fill' (cover-crop) instead of 'span'."
            )

        cli = shutil.which("plasma-apply-wallpaperimage")
        if cli:
            try:
                proc = subprocess.run(
                    [cli, "--fill-mode", fill_name, str(path)],
                    capture_output=True, text=True, timeout=_EVALUATE_SCRIPT_TIMEOUT,
                )
                if proc.returncode == 0:
                    return
                logging.warning(
                    "plasma-apply-wallpaperimage failed (exit %d): %s; falling back "
                    "to D-Bus scripting.", proc.returncode, proc.stderr.strip(),
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logging.warning(
                    "Failed to run plasma-apply-wallpaperimage (%s); falling back "
                    "to D-Bus scripting.", exc,
                )

        script = (
            "var ds = desktops();"
            "for (var i = 0; i < ds.length; i++) {"
            "  ds[i].wallpaperPlugin = 'org.kde.image';"
            "  ds[i].currentConfigGroup = ['Wallpaper', 'org.kde.image', 'General'];"
            f"  ds[i].writeConfig('Image', {json.dumps(str(path))});"
            f"  ds[i].writeConfig('FillMode', {fill_value});"
            "}"
        )
        if self._run_evaluate_script(script) is None:
            logging.error(
                "Failed to apply KDE wallpaper via both plasma-apply-wallpaperimage "
                "and D-Bus scripting."
            )

    def list_monitors(self) -> list[MonitorInfo]:
        screens = self._query_screens()
        if not screens:
            return []
        monitors = [
            MonitorInfo(str(s["screen"]), s["left"], s["top"],
                        s["left"] + s["width"], s["top"] + s["height"])
            for s in screens
        ]
        monitors.sort(key=lambda m: m.left)
        return monitors

    def apply_wallpaper_per_monitor(self, assignments: dict[str, Path], style: str) -> None:
        if not assignments:
            return
        _, fill_value = _FILL_MODES.get(style, _FILL_MODES["fill"])
        if style == "span":
            logging.warning(
                "KDE has no native multi-monitor wallpaper-spanning primitive; "
                "falling back to 'fill' (cover-crop) instead of 'span'."
            )

        # mon_id round-trips through list_monitors()'s str(screen index) ids --
        # this method only ever receives ids this backend itself produced.
        payload = [
            {"screen": int(mon_id), "path": str(path), "fillMode": fill_value}
            for mon_id, path in assignments.items()
        ]
        script = (
            f"var assignments = {json.dumps(payload)};"
            "var ds = desktops();"
            "assignments.forEach(function(a) {"
            "  for (var i = 0; i < ds.length; i++) {"
            "    if (ds[i].screen === a.screen) {"
            "      ds[i].wallpaperPlugin = 'org.kde.image';"
            "      ds[i].currentConfigGroup = ['Wallpaper', 'org.kde.image', 'General'];"
            "      ds[i].writeConfig('Image', a.path);"
            "      ds[i].writeConfig('FillMode', a.fillMode);"
            "    }"
            "  }"
            "});"
        )
        if self._run_evaluate_script(script) is None:
            logging.error("Failed to apply per-monitor KDE wallpapers via D-Bus scripting.")

    def get_power_state(self) -> PowerState:
        upower = shutil.which("upower")
        if not upower:
            return PowerState(on_battery=None)
        try:
            proc = subprocess.run(
                [upower, "-i", "/org/freedesktop/UPower/devices/DisplayDevice"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return PowerState(on_battery=None)
        if proc.returncode != 0:
            return PowerState(on_battery=None)

        state = None
        percent = None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("state:"):
                state = line.split(":", 1)[1].strip()
            elif line.startswith("percentage:"):
                percent = line.split(":", 1)[1].strip().rstrip("%")
        if state is None:
            return PowerState(on_battery=None)
        try:
            battery_percent = float(percent) if percent else None
        except ValueError:
            battery_percent = None
        return PowerState(on_battery=(state == "discharging"), battery_percent=battery_percent)

    def is_network_metered(self) -> bool | None:
        nmcli = shutil.which("nmcli")
        if not nmcli:
            return None
        try:
            proc = subprocess.run(
                [nmcli, "-t", "-f", "DEVICE,STATE", "device"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if proc.returncode != 0:
            return None

        device = None
        for line in proc.stdout.splitlines():
            name, _, state = line.partition(":")
            if state == "connected":
                device = name
                break
        if not device:
            return None

        try:
            proc = subprocess.run(
                [nmcli, "-t", "-g", "GENERAL.METERED", "device", "show", device],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if proc.returncode != 0:
            return None

        value = proc.stdout.strip().lower()
        if value in ("yes", "guess-yes"):
            return True
        if value in ("no", "guess-no"):
            return False
        return None

    def default_data_dir(self) -> Path:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
        return base / "goes-wallpaper"

    def default_font_path(self) -> str:
        # Noto Sans has been Plasma's own default UI font since 5.18 and ships as a
        # base font package on most distros' KDE spins -- confirmed present at this
        # exact path via `fc-match sans-serif` on a real Plasma/Debian install.
        # Callers already fall back to Pillow's built-in font if this path doesn't
        # exist (see WallpaperPlatform.default_font_path's docstring), so a distro
        # that puts it elsewhere degrades gracefully rather than breaking the info
        # bar.
        return "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
