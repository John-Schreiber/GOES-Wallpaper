# tests/test_geolocation.py -- lonlat_to_pixels regression vs validated city landmarks
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test for the CONUS and Full Disk georeferencing calibration
(_GEOS_AREA_CONUS/_GEOS_AREA_FULL_DISK + lonlat_to_pixels). The CONUS pixel
coordinates below were computed with this same function and confirmed correct by
marking them on real fetched CONUS frames and visually checking each landmark lands
on the right city/coastline (see the conversation this was built in, or
CUSTOM_IMAGERY_PLAN.md/NEXT_STEPS.md for context) -- it is NOT itself proof of
real-world accuracy (that was established once, visually, against live imagery); it
only proves "the math still gives the same answer it gave when we checked it against
reality." The Full Disk coordinates instead cross-check against pyresample's own
AreaDefinition for satpy's shipped goes_west/east_abi_f_2km areas (same underlying
constants, independent code path), since there's no visually-checked live Full Disk
frame to compare against here."""

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


# (satellite, place): (lon, lat, expected_col, expected_row) at 5424x5424 (matches
# satpy's own goes_west/east_abi_f_2km area exactly, so these can be cross-checked
# against pyresample's AreaDefinition.get_array_coordinates_from_lonlat for the same
# area -- independent of this module's own _project_to_pixels math). pyresample's
# array-coordinate convention indexes pixel *centers* (center of pixel 0 is at 0);
# lonlat_to_pixels/_project_to_pixels instead returns edge-based continuous
# coordinates (0 is the left/top edge of pixel 0) to match PIL's own drawing
# convention, so pyresample's raw value + 0.5 is the expected col/row here.
FULL_DISK_LANDMARKS = {
    ("GOES18", "subpoint"): (-137.0, 0.0, 2712.00, 2712.00),
    ("GOES18", "Denver"): (-104.99, 39.74, 3926.57, 813.24),
    ("GOES18", "Auckland"): (174.76, -36.85, 969.87, 4458.95),
    ("GOES19", "subpoint"): (-75.0, 0.0, 2712.00, 2712.00),
    ("GOES19", "NYC"): (-74.01, 40.71, 2751.78, 740.21),
    ("GOES19", "London"): (-0.13, 51.51, 4362.55, 564.81),
    ("GOES19", "Rio"): (-43.17, -22.91, 4194.63, 3895.71),
}


@pytest.mark.parametrize("key", list(FULL_DISK_LANDMARKS), ids=lambda k: f"{k[0]}-{k[1]}")
def test_full_disk_landmark_matches_pyresample(key):
    satellite, _place = key
    lon, lat, expected_col, expected_row = FULL_DISK_LANDMARKS[key]
    col, row = gw.lonlat_to_pixels(satellite, np.array([lon]), np.array([lat]), 5424, 5424, "FD")
    assert col[0] == pytest.approx(expected_col, abs=0.1)
    assert row[0] == pytest.approx(expected_row, abs=0.1)


def test_full_disk_unsupported_satellite_returns_none():
    assert gw.lonlat_to_pixels("GOES16", np.array([-100.0]), np.array([40.0]), 5424, 5424, "FD") is None


def test_mesoscale_sector_returns_none():
    # Mesoscale sectors move -- no fixed extent to hardcode, unlike CONUS/FD.
    assert gw.lonlat_to_pixels("GOES18", np.array([-100.0]), np.array([40.0]), 1000, 1000, "M1") is None


def test_lonlat_to_pixels_area_matches_lonlat_to_pixels_for_equivalent_area():
    """lonlat_to_pixels_area (satpy_raw path, any sector) reuses the same linear
    fraction-of-extent math as lonlat_to_pixels (cdn_jpg path, CONUS only) -- feeding
    it the same proj4/extent GOES-18 uses should reproduce identical pixel positions
    for the already-validated landmarks above, without needing a separate real
    satpy-derived fixture."""
    lon, lat, expected_col, expected_row = KNOWN_LANDMARKS[("GOES18", "SF")]
    area = gw.AreaInfo(
        proj4_params={
            "proj": "geos", "sweep": "x", "lon_0": -137.0, "h": 35786023,
            "x_0": 0, "y_0": 0, "ellps": "GRS80", "units": "m",
        },
        extent=(-2505021.61, 1583173.65752, 2505021.61, 4589199.58952),
    )
    col, row = gw.lonlat_to_pixels_area(area, np.array([lon]), np.array([lat]), 2500, 1500)
    assert col[0] == pytest.approx(expected_col, abs=0.5)
    assert row[0] == pytest.approx(expected_row, abs=0.5)
