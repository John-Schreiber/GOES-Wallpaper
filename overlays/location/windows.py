#!/usr/bin/env python3
# overlays/location/windows.py -- example shell_sources provider: "you are here"
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Example [[shell_sources]] command (see OVERLAYS.md) that prints a single
GeoJSON Point at the computer's current location, via WinRT's
Windows.Devices.Geolocation API -- the same winrt-py binding family
platform_windows.py already uses for power/network state and the lock screen
(see its module docstring), following the same calling conventions (snake_case
async methods, asyncio.run, OSError on failure).

Needs the companion winrt package, not part of this project's own dependencies
(this is an opt-in example, not core functionality):
    uv pip install winrt-windows-devices-geolocation
(or `pip install winrt-windows-devices-geolocation` in whatever environment
runs this script -- it doesn't have to be the same venv goes_wallpaper.py uses,
since shell_sources just runs a command and reads its stdout.)

Requires Windows location services to be on, and "Let desktop apps access your
location" enabled, under Settings > Privacy & security > Location -- for a
classic (non-UWP) console caller like this, that Settings toggle is what
actually gates access; **no permission prompt appears the way it would for a
packaged app**, confirmed by testing (RequestAccessAsync silently reflects the
existing toggle state instead of raising a dialog). If the toggle is off,
GeolocationAccessStatus comes back DENIED/UNSPECIFIED and this exits with that
in the message -- there's nothing to "grant" interactively, only the Settings
page.

Even with access granted, `get_geoposition_async()` can hang for a long time
(or effectively forever) on a desktop with no GPS hardware if it's asking for
high-accuracy positioning, which prefers GPS over the network/Wi-Fi-based
positioning desktops actually have available -- this script sets
`desired_accuracy` to Default (not High) specifically to avoid that, and wraps
the whole fetch in a hard timeout so it fails with a clear message instead of
hanging silently, rather than the previous version's unbounded wait.

**Confirmed working against real hardware**: on a real Windows machine, the
original version (no accuracy setting, no timeout) ran with no error and no
output -- consistent with an unbounded hang requesting High-accuracy (GPS)
positioning on a desktop with no GPS hardware. Setting `desired_accuracy` to
Default (network/Wi-Fi-based positioning instead) resolved it: access granted
with no prompt (as expected for a non-packaged caller -- see above) and a real
position fix returned successfully.

Usage (see the [[shell_sources]] example in OVERLAYS.md):
    [[shell_sources]]
    name = "here"
    command = ["python", "overlays/location/windows.py"]
    timeout = 30.0
    marker_radius = 10
"""
from __future__ import annotations

import asyncio
import json
import sys

_POSITION_TIMEOUT_SECONDS = 20.0


def _log(message: str) -> None:
    # stdout must stay clean JSON-only for shell_sources to parse -- diagnostics
    # go to stderr, which fetch_shell_geojson surfaces in its own log line if
    # this exits non-zero, and which is visible directly when run standalone.
    print(message, file=sys.stderr)


def main() -> None:
    from winrt.windows.devices.geolocation import Geolocator, GeolocationAccessStatus, PositionAccuracy

    async def get_position() -> tuple[float, float]:
        _log("Requesting location access...")
        access = await Geolocator.request_access_async()
        _log(f"Access status: {access}")
        if access != GeolocationAccessStatus.ALLOWED:
            raise SystemExit(
                f"Location access not granted (status={access}). No prompt appears for "
                "this kind of (non-packaged) caller -- enable access directly under "
                "Settings > Privacy & security > Location, including 'Let desktop apps "
                "access your location'."
            )
        geolocator = Geolocator()
        geolocator.desired_accuracy = PositionAccuracy.DEFAULT  # avoid waiting on GPS hardware desktops don't have
        _log(f"Waiting up to {_POSITION_TIMEOUT_SECONDS:.0f}s for a position fix...")
        try:
            position = await asyncio.wait_for(geolocator.get_geoposition_async(), timeout=_POSITION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise SystemExit(
                f"No position fix within {_POSITION_TIMEOUT_SECONDS:.0f}s -- access was granted, "
                "but Windows never returned a location. Likely a network-based-positioning "
                "issue at the OS level (this desktop's Wi-Fi/network location signal), not "
                "something this script controls."
            ) from None
        _log("Got a position fix.")
        point = position.coordinate.point.position
        return point.latitude, point.longitude

    try:
        lat, lon = asyncio.run(get_position())
    except OSError as e:
        raise SystemExit(f"Couldn't get location via WinRT Geolocator: {e}") from None

    feature = {
        "type": "Feature",
        "properties": {"name": "Here", "marker-symbol": "marker"},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }
    print(json.dumps(feature))


if __name__ == "__main__":
    main()
