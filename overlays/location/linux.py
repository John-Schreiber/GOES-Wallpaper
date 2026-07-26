#!/usr/bin/env python3
# overlays/location/linux.py -- example shell_sources provider: "you are here"
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Example [[shell_sources]] command (see OVERLAYS.md) that prints a single
GeoJSON Point at the computer's current location, via GeoClue2
(https://gitlab.freedesktop.org/geoclue/geoclue) -- the D-Bus geolocation
service GNOME/KDE both use. Talks to it via `gdbus call` (part of glib2,
present on virtually every Linux desktop) rather than a Python D-Bus binding
(`dbus-python`/`pydbus`), so this needs no extra pip install -- the same
"shell out to a system tool already on the box" choice platform_linux_kde.py
makes for `upower`/`nmcli`/`qdbus`, rather than a compiled D-Bus extension.

Same session caveat platform_linux_kde.py's module docstring already documents
for KDE's own D-Bus wallpaper scripting: needs a real logged-in desktop session
(DBUS_SESSION_BUS_ADDRESS pointing at it) -- a headless cron job or a systemd
*system* (not --user) service won't have that. GeoClue2 also requires an
agent/authorization mechanism to actually grant access (typically satisfied by
running under a normal desktop session where a geoclue agent -- e.g. GNOME's or
KDE's -- is already running).

**Unverified against a real GeoClue2 session** -- no Linux desktop was
available to test this end to end; written directly from GeoClue2's documented
D-Bus API (see the link above). `gdbus call`'s output is a GVariant-literal
text format, parsed here with regexes rather than a real D-Bus binding, which
is the most likely thing to need adjusting if GeoClue2's output doesn't match
what's expected below -- run the `gdbus call` commands in
_get_client/_start/_poll_location_path/_read_location by hand first to compare
their actual output if this doesn't work.

Usage (see the [[shell_sources]] example in OVERLAYS.md):
    [[shell_sources]]
    name = "here"
    command = ["python3", "overlays/location/linux.py"]
    timeout = 15.0
    marker_radius = 10
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

_MANAGER = "/org/freedesktop/GeoClue2/Manager"
_SERVICE = "org.freedesktop.GeoClue2"
_OBJECT_PATH_RE = re.compile(r"objectpath '([^']*)'")
_POLL_INTERVAL_SECONDS = 1.0
_POLL_TIMEOUT_SECONDS = 12.0


def _gdbus_call(object_path: str, interface: str, method: str, *args: str) -> str:
    result = subprocess.run(
        ["gdbus", "call", "--system", "--dest", _SERVICE, "--object-path", object_path,
         "--method", f"{interface}.{method}", *args],
        capture_output=True, text=True, timeout=10.0,
    )
    if result.returncode != 0:
        raise SystemExit(f"gdbus call {interface}.{method} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _get_client() -> str:
    output = _gdbus_call(_MANAGER, "org.freedesktop.GeoClue2.Manager", "GetClient")
    match = _OBJECT_PATH_RE.search(output)
    if not match:
        raise SystemExit(f"Couldn't parse client object path from: {output!r}")
    return match.group(1)


def _start(client_path: str) -> None:
    # DesktopId identifies the requesting app to GeoClue2/its authorization
    # agent -- required, not just informational.
    _gdbus_call(
        client_path, "org.freedesktop.DBus.Properties", "Set",
        "org.freedesktop.GeoClue2.Client", "DesktopId", "<'goes-wallpaper'>",
    )
    _gdbus_call(client_path, "org.freedesktop.GeoClue2.Client", "Start")


def _poll_location_path(client_path: str) -> str:
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        output = _gdbus_call(
            client_path, "org.freedesktop.DBus.Properties", "Get",
            "org.freedesktop.GeoClue2.Client", "Location",
        )
        match = _OBJECT_PATH_RE.search(output)
        if match and match.group(1) not in ("", "/"):
            return match.group(1)
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise SystemExit(f"No location fix from GeoClue2 within {_POLL_TIMEOUT_SECONDS:.0f}s")


def _read_location(location_path: str) -> tuple[float, float]:
    output = _gdbus_call(
        location_path, "org.freedesktop.DBus.Properties", "GetAll",
        "org.freedesktop.GeoClue2.Location",
    )
    lat_match = re.search(r"'Latitude':\s*<([-\d.]+)>", output)
    lon_match = re.search(r"'Longitude':\s*<([-\d.]+)>", output)
    if not (lat_match and lon_match):
        raise SystemExit(f"Couldn't parse Latitude/Longitude from: {output!r}")
    return float(lat_match.group(1)), float(lon_match.group(1))


def main() -> None:
    try:
        client_path = _get_client()
        _start(client_path)
        location_path = _poll_location_path(client_path)
        lat, lon = _read_location(location_path)
    except FileNotFoundError:
        raise SystemExit("gdbus not found -- install glib2/libglib2.0-bin (usually already present)") from None

    feature = {
        "type": "Feature",
        "properties": {"name": "Here", "marker-symbol": "marker"},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }
    print(json.dumps(feature))


if __name__ == "__main__":
    main()
