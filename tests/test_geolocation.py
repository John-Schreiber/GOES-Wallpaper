# tests/test_geolocation.py -- lonlat_to_pixels regression vs validated city landmarks
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test for the CONUS georeferencing calibration (_GEOS_AREA_CONUS +
lonlat_to_pixels). The expected pixel coordinates below were computed with this same
function and confirmed correct by marking them on real fetched CONUS frames and
visually checking each landmark lands on the right city/coastline (see the
conversation this was built in, or CUSTOM_IMAGERY_PLAN.md/NEXT_STEPS.md for context).
This test guards against a future change to the transform math or the calibration
constants silently breaking overlay accuracy — it is NOT itself proof of real-world
accuracy (that was established once, visually, against live imagery); it only proves
"the math still gives the same answer it gave when we checked it against reality."""

import numpy as np
import pytest

import goes_wallpaper as gw

# (satellite, city, lon, lat): (expected_col, expected_row) at 2500x1500
KNOWN_LANDMARKS = {
    ("GOES18", "SF"): (-122.42, 37.77, 1855.21, 435.57),
    ("GOES18", "LA"): (-118.24, 34.05, 2063.41, 588.50),
    ("GOES18", "Seattle"): (-122.33, 47.61, 1758.72, 98.00),
    ("GOES18", "Denver"): (-104.99, 39.74, 2464.57, 391.24),
    ("GOES18", "Phoenix"): (-112.07, 33.45, 2318.08, 622.81),
    ("GOES19", "NYC"): (-74.01, 40.71, 1849.78, 318.21),
    ("GOES19", "Miami"): (-80.19, 25.76, 1555.94, 942.42),
    ("GOES19", "Chicago"): (-87.63, 41.88, 1318.37, 282.76),
    ("GOES19", "Atlanta"): (-84.39, 33.75, 1393.12, 591.27),
    ("GOES19", "Boston"): (-71.06, 42.36, 1963.63, 260.52),
}


@pytest.mark.parametrize("key", list(KNOWN_LANDMARKS), ids=lambda k: f"{k[0]}-{k[1]}")
def test_known_landmark_within_half_pixel(key):
    satellite, _city = key
    lon, lat, expected_col, expected_row = KNOWN_LANDMARKS[key]
    col, row = gw.lonlat_to_pixels(satellite, np.array([lon]), np.array([lat]), 2500, 1500)
    assert col[0] == pytest.approx(expected_col, abs=0.5)
    assert row[0] == pytest.approx(expected_row, abs=0.5)


def test_scales_correctly_to_a_different_resolution():
    # Same lon/lat, different requested frame size -> proportionally scaled pixel position.
    lon, lat = KNOWN_LANDMARKS[("GOES18", "SF")][:2]
    col_2500, row_1500 = gw.lonlat_to_pixels("GOES18", np.array([lon]), np.array([lat]), 2500, 1500)
    col_5000, row_3000 = gw.lonlat_to_pixels("GOES18", np.array([lon]), np.array([lat]), 5000, 3000)
    assert col_5000[0] == pytest.approx(col_2500[0] * 2, abs=0.01)
    assert row_3000[0] == pytest.approx(row_1500[0] * 2, abs=0.01)


def test_unsupported_satellite_returns_none():
    assert gw.lonlat_to_pixels("GOES16", np.array([-100.0]), np.array([40.0]), 2500, 1500) is None


def test_east_and_west_satellites_give_different_pixel_positions_for_same_point():
    lon, lat = -100.0, 40.0
    col_w, row_w = gw.lonlat_to_pixels("GOES18", np.array([lon]), np.array([lat]), 2500, 1500)
    col_e, row_e = gw.lonlat_to_pixels("GOES19", np.array([lon]), np.array([lat]), 2500, 1500)
    assert (col_w[0], row_w[0]) != (col_e[0], row_e[0])
