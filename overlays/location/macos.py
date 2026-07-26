#!/usr/bin/env python3
# overlays/location/macos.py -- example shell_sources provider: "you are here"
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Example [[shell_sources]] command (see OVERLAYS.md) that prints a single
GeoJSON Point at the computer's current location, via CoreLocation --
specifically the third-party `CoreLocationCLI` tool
(https://github.com/fulldecent/corelocationcli), not a hand-rolled pyobjc
CoreLocation binding. Reasoning: CoreLocation's permission prompt is tied to
app/bundle identity, so a bare Python script driving CLLocationManager directly
often doesn't trigger (or persist) the permission prompt the way a properly
signed .app does -- CoreLocationCLI has already solved that problem, the same
way platform_macos.py prefers pyobjc's real AppKit/NSWorkspace bindings for
things that *do* work unpackaged (wallpaper apply, screen geometry) but shells
out to `pmset` for battery state rather than fighting a framework binding for
something a plain CLI already handles.

Install:
    brew install corelocationcli

First run prompts for Location Services permission (System Settings > Privacy
& Security > Location Services) -- grant it there if the prompt doesn't appear
or was dismissed.

**Unverified against real hardware** -- no Mac was available to test this
against a live location fix; written from CoreLocationCLI's documented output
format. If its output format has changed, adjust _parse_output below --
everything else (the GeoJSON shape, the shell_sources wiring) is unaffected.

Usage (see the [[shell_sources]] example in OVERLAYS.md):
    [[shell_sources]]
    name = "here"
    command = ["python3", "overlays/location/macos.py"]
    timeout = 15.0
    marker_radius = 10
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# CoreLocationCLI's default (non-JSON) output is a single line like
# "37.774900,-122.419400" (lat,lon) -- some versions print extra fields after a
# third comma (altitude, etc.), which this ignores.
_LAT_LON_RE = re.compile(r"(-?\d+\.\d+),\s*(-?\d+\.\d+)")


def _parse_output(text: str) -> tuple[float, float]:
    match = _LAT_LON_RE.search(text)
    if not match:
        raise SystemExit(f"Couldn't parse CoreLocationCLI output: {text!r}")
    return float(match.group(1)), float(match.group(2))


def main() -> None:
    try:
        result = subprocess.run(
            ["CoreLocationCLI"], capture_output=True, text=True, timeout=10.0,
        )
    except FileNotFoundError:
        raise SystemExit("CoreLocationCLI not found -- install with: brew install corelocationcli") from None
    except subprocess.TimeoutExpired:
        raise SystemExit("CoreLocationCLI timed out waiting for a location fix") from None

    if result.returncode != 0:
        raise SystemExit(f"CoreLocationCLI failed (exit {result.returncode}): {result.stderr.strip()}")

    lat, lon = _parse_output(result.stdout)
    feature = {
        "type": "Feature",
        "properties": {"name": "Here", "marker-symbol": "marker"},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }
    print(json.dumps(feature))


if __name__ == "__main__":
    main()
