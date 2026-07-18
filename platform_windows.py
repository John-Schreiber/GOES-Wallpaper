# platform_windows.py -- Windows backend for platform_base.WallpaperPlatform
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

"""Windows backend for platform_base.WallpaperPlatform — reference implementation.

Every piece here was validated against real hardware during development (see the
conversation goes_wallpaper.py was built in): GetSystemMetrics/WMI screen-size
fallback, the Shell_TrayWnd taskbar height, SystemParametersInfoW + registry style,
the IDesktopWallpaper COM interface for per-monitor wallpapers (against a real
2-monitor setup), and battery/network-cost detection via the `winrt-Windows.System.
Power`/`winrt-Windows.Networking.Connectivity` packages. Battery and network cost
were both first attempted via hand-rolled classic-COM bindings (GetSystemPowerStatus
ctypes struct, INetworkCostManager); the winrt packages replaced both — same
underlying OS data, but generated from Windows' own metadata instead of hand-typed
GUIDs/struct layouts, and the INetworkCostManager COM binding specifically failed to
activate in initial testing here while its winrt equivalent worked immediately.
Desktop wallpaper application and taskbar geometry have no WinRT equivalent (those
were never exposed outside classic Win32/COM), so they stay as ctypes/COM.
"""

from __future__ import annotations

import ctypes
import json
import logging
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import NamedTuple

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from winrt.windows.networking.connectivity import NetworkCostType, NetworkInformation
from winrt.windows.system.power import BatteryStatus, PowerManager

from platform_base import MonitorInfo, PowerState, WallpaperPlatform

_NONINTERACTIVE_DEFAULT_SIZE = (1024, 768)

class _StyleCodes(NamedTuple):
    """The two independent Windows numeric schemes for one wallpaper_style value:
    the legacy Control Panel\\Desktop registry pair (used by apply_wallpaper, the
    single-monitor SystemParametersInfoW path) and the DESKTOP_WALLPAPER_POSITION enum
    (shobjidl.h, used by IDesktopWallpaper::SetPosition, the per-monitor path). Kept as
    one entry per style so the two schemes can't drift out of sync with each other."""
    registry_style: str
    registry_tile: str
    position: int


_WALLPAPER_STYLE_CODES = {
    "fill": _StyleCodes("10", "0", 4),
    "fit": _StyleCodes("6", "0", 3),
    "stretch": _StyleCodes("2", "0", 2),
    "tile": _StyleCodes("0", "1", 1),
    "center": _StyleCodes("0", "0", 0),
    "span": _StyleCodes("22", "0", 5),
}

_CLSID_DESKTOP_WALLPAPER = GUID("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")


class _IDesktopWallpaper(IUnknown):
    """Minimal binding for shobjidl.h's IDesktopWallpaper — lets each monitor get an
    independently set wallpaper image, unlike SystemParametersInfoW which only sets
    one image for the whole desktop."""
    _iid_ = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetWallpaper",
                  (['in'], ctypes.c_wchar_p, "monitorID"),
                  (['in'], ctypes.c_wchar_p, "wallpaper")),
        COMMETHOD([], HRESULT, "GetWallpaper",
                  (['in'], ctypes.c_wchar_p, "monitorID"),
                  (['out'], ctypes.POINTER(ctypes.c_wchar_p), "wallpaper")),
        COMMETHOD([], HRESULT, "GetMonitorDevicePathAt",
                  (['in'], ctypes.c_uint, "monitorIndex"),
                  (['out'], ctypes.POINTER(ctypes.c_wchar_p), "monitorID")),
        COMMETHOD([], HRESULT, "GetMonitorDevicePathCount",
                  (['out'], ctypes.POINTER(ctypes.c_uint), "count")),
        COMMETHOD([], HRESULT, "GetMonitorRECT",
                  (['in'], ctypes.c_wchar_p, "monitorID"),
                  (['out'], ctypes.POINTER(wintypes.RECT), "displayRect")),
        COMMETHOD([], HRESULT, "SetBackgroundColor",
                  (['in'], ctypes.c_uint, "color")),
        COMMETHOD([], HRESULT, "GetBackgroundColor",
                  (['out'], ctypes.POINTER(ctypes.c_uint), "color")),
        COMMETHOD([], HRESULT, "SetPosition",
                  (['in'], ctypes.c_int, "position")),
        COMMETHOD([], HRESULT, "GetPosition",
                  (['out'], ctypes.POINTER(ctypes.c_int), "position")),
        COMMETHOD([], HRESULT, "SetSlideshow",
                  (['in'], ctypes.POINTER(IUnknown), "items")),
        COMMETHOD([], HRESULT, "GetSlideshow",
                  (['out'], ctypes.POINTER(ctypes.POINTER(IUnknown)), "items")),
        COMMETHOD([], HRESULT, "SetSlideshowOptions",
                  (['in'], ctypes.c_int, "options"),
                  (['in'], ctypes.c_uint, "slideshowTick")),
        COMMETHOD([], HRESULT, "GetSlideshowOptions",
                  (['out'], ctypes.POINTER(ctypes.c_int), "options"),
                  (['out'], ctypes.POINTER(ctypes.c_uint), "slideshowTick")),
        COMMETHOD([], HRESULT, "AdvanceSlideshow",
                  (['in'], ctypes.c_wchar_p, "monitorID"),
                  (['in'], ctypes.c_int, "direction")),
        COMMETHOD([], HRESULT, "GetStatus",
                  (['out'], ctypes.POINTER(ctypes.c_int), "state")),
        COMMETHOD([], HRESULT, "Enable",
                  (['in'], wintypes.BOOL, "fEnable")),
    ]


def _query_wmi_resolution() -> tuple[int, int] | None:
    """Ask WMI for the video driver's current display mode. Unlike GetSystemMetrics,
    this reads the driver/hardware state directly rather than the calling process's
    window station, so it still works when the process has no interactive desktop."""
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance -ClassName Win32_VideoController | "
                "Where-Object { $_.CurrentHorizontalResolution } | "
                "Select-Object -First 1 CurrentHorizontalResolution,CurrentVerticalResolution | "
                "ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        data = json.loads(proc.stdout)
        width = int(data["CurrentHorizontalResolution"])
        height = int(data["CurrentVerticalResolution"])
        if width > 0 and height > 0:
            return width, height
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        pass
    return None


class WindowsPlatform(WallpaperPlatform):
    def get_screen_size(
        self,
        span_all_monitors: bool,
        width_override: int | None,
        height_override: int | None,
        use_fallback_detection: bool = True,
    ) -> tuple[int, int]:
        if width_override and height_override:
            return width_override, height_override

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        if span_all_monitors:
            width = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        else:
            width = user32.GetSystemMetrics(0)   # SM_CXSCREEN
            height = user32.GetSystemMetrics(1)  # SM_CYSCREEN

        if (width, height) != _NONINTERACTIVE_DEFAULT_SIZE:
            return width, height

        if use_fallback_detection and not span_all_monitors:
            wmi_size = _query_wmi_resolution()
            if wmi_size:
                logging.info(
                    "GetSystemMetrics returned the non-interactive default (1024x768); "
                    "using WMI-reported resolution %s instead", wmi_size,
                )
                return wmi_size

        logging.warning(
            "Detected screen size 1024x768 — this is Windows' fallback for a "
            "non-interactive session (e.g. a scheduled task with no user logged on), "
            "and the WMI fallback %s. If this wallpaper looks wrong, set "
            "screen_width/screen_height explicitly in config.toml or run the task "
            "only when a user is logged on.",
            "didn't find a usable resolution either" if use_fallback_detection else "is disabled",
        )
        return width, height

    def get_taskbar_height(self) -> int:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return 0
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0
        return max(0, rect.bottom - rect.top)

    def apply_wallpaper(self, path: Path, style: str) -> None:
        import winreg

        codes = _WALLPAPER_STYLE_CODES.get(style, _WALLPAPER_STYLE_CODES["fill"])
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, codes.registry_style)
            winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, codes.registry_tile)

        SPI_SETDESKWALLPAPER = 0x14
        SPIF_UPDATEINIFILE = 0x1
        SPIF_SENDWININICHANGE = 0x2
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, str(path), SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
        )

    def list_monitors(self) -> list[MonitorInfo]:
        comtypes.CoInitialize()
        try:
            idw = comtypes.client.CreateObject(_CLSID_DESKTOP_WALLPAPER, interface=_IDesktopWallpaper)
            monitors = []
            count = idw.GetMonitorDevicePathCount()
            for i in range(count):
                mon_id = idw.GetMonitorDevicePathAt(i)
                try:
                    rect = idw.GetMonitorRECT(mon_id)
                except comtypes.COMError:
                    # Windows can report extra "known but disconnected" device paths
                    # that error on GetMonitorRECT; skip them.
                    continue
                monitors.append(MonitorInfo(mon_id, rect.left, rect.top, rect.right, rect.bottom))
            return monitors
        finally:
            comtypes.CoUninitialize()

    def apply_wallpaper_per_monitor(self, assignments: dict[str, Path], style: str) -> None:
        if not assignments:
            return
        comtypes.CoInitialize()
        try:
            idw = comtypes.client.CreateObject(_CLSID_DESKTOP_WALLPAPER, interface=_IDesktopWallpaper)
            idw.SetPosition(_WALLPAPER_STYLE_CODES.get(style, _WALLPAPER_STYLE_CODES["fill"]).position)
            for mon_id, path in assignments.items():
                idw.SetWallpaper(mon_id, str(path))
        finally:
            comtypes.CoUninitialize()

    def get_power_state(self) -> PowerState:
        """Via winrt-Windows.System.Power (verified against real hardware: correctly
        reports BatteryStatus.NOT_PRESENT / on_battery=False on this desktop)."""
        try:
            status = PowerManager.battery_status
            if status == BatteryStatus.NOT_PRESENT:
                return PowerState(on_battery=False)
            pct = PowerManager.remaining_charge_percent
            return PowerState(on_battery=(status == BatteryStatus.DISCHARGING), battery_percent=pct)
        except OSError:
            return PowerState(on_battery=None)

    def is_network_metered(self) -> bool | None:
        """Via winrt-Windows.Networking.Connectivity (verified against real
        hardware: correctly reports NetworkCostType.UNRESTRICTED on this machine's
        unmetered connection)."""
        try:
            profile = NetworkInformation.get_internet_connection_profile()
            if profile is None:
                return None
            cost_type = profile.get_connection_cost().network_cost_type
            return cost_type in (NetworkCostType.FIXED, NetworkCostType.VARIABLE)
        except OSError:
            return None

    def default_data_dir(self) -> Path:
        return Path.home() / "AppData" / "Local" / "GOES-Wallpaper"

    def default_font_path(self) -> str:
        return r"C:\Windows\Fonts\segoeui.ttf"
