#!/usr/bin/env python3
# overlays/fetch_earthquakes.py -- example shell_sources provider: recent earthquakes
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Example [[shell_sources]] command (see OVERLAYS.md) that fetches one of
USGS's public earthquake feeds (already real GeoJSON -- see
https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php for the full list
of feeds and their update cadence) and re-emits it with simplestyle
`marker-size`/`marker-color` set from each quake's magnitude, plus a `name`
label, so it's ready to drop straight into a shell_sources entry without any
further transformation -- a real example of the "live third-party GeoJSON
feed" use case OVERLAYS.md's shell_sources section describes (alongside NHC
storm tracks/NIFC fire perimeters).

No extra dependency -- stdlib `urllib.request` only, since this is just a
plain HTTPS GET, unlike overlays/location/*.py which each need an OS-specific
geolocation backend.

Usage (see the [[shell_sources]] example in OVERLAYS.md):
    [[shell_sources]]
    name = "earthquakes"
    command = ["python", "overlays/fetch_earthquakes.py"]
    timeout = 15.0
    marker_radius = 6
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# "Significant earthquakes, past week" -- a manageable number of features for a
# wallpaper overlay. Swap for any other USGS feed (more/less frequent, lower/
# higher magnitude threshold) by changing just this URL -- see the docs link
# above for the full list, e.g.:
#   .../summary/2.5_day.geojson    -- M2.5+, past day
#   .../summary/4.5_week.geojson   -- M4.5+, past week
#   .../summary/all_day.geojson    -- every detected quake, past day (a lot)
FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.geojson"
TIMEOUT_SECONDS = 10.0


def _style_for_magnitude(mag: float | None) -> tuple[str, str]:
    """simplestyle marker-size/marker-color from magnitude -- a rough severity
    scale, not a scientific one."""
    if mag is None:
        return "medium", "#ffcc00"
    if mag >= 6.0:
        return "large", "#cc0000"
    if mag >= 4.5:
        return "medium", "#ff9900"
    return "small", "#ffcc00"


def main() -> None:
    try:
        with urllib.request.urlopen(FEED_URL, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise SystemExit(f"Couldn't fetch/parse USGS feed: {e}") from None

    for feature in payload.get("features", []):
        props = feature.setdefault("properties", {})
        mag = props.get("mag")
        props["marker-size"], props["marker-color"] = _style_for_magnitude(mag)
        place = props.get("place") or "Unknown location"
        props["name"] = f"M{mag:.1f} - {place}" if isinstance(mag, (int, float)) else place

    print(json.dumps(payload))


if __name__ == "__main__":
    main()
