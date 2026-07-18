# tests/test_lonlat_crop.py -- lonlat_box_to_crop_fraction (lat/lon region-of-interest crop)
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers lonlat_box_to_crop_fraction: converts a lon/lat bounding box into the
(left, top, right, bottom) pixel-fraction crop crop_fractional expects, using the
same georeferencing calibration draw_overlays/lonlat_to_pixels use. Expected crop
fractions below are derived from test_geolocation.py's already-validated
KNOWN_LANDMARKS (GOES18/CONUS), not re-derived here."""

import numpy as np
import pytest

import goes_wallpaper as gw

IMG_W, IMG_H = 2500, 1500

# GOES18/CONUS landmarks from test_geolocation.KNOWN_LANDMARKS, at 2500x1500:
# Denver (-104.99, 39.74) -> (2464.57, 391.24); Phoenix (-112.07, 33.45) -> (2318.08, 622.81)
DENVER = (-104.99, 39.74, 2464.57, 391.24)
PHOENIX = (-112.07, 33.45, 2318.08, 622.81)


def test_box_matches_bounding_box_of_its_4_projected_corners():
    # The function projects all 4 corners of the lon/lat box (not just Denver/Phoenix
    # themselves -- 2 of the box's 4 corners are hybrid points, e.g. (Denver's lon,
    # Phoenix's lat), which project to yet another pixel position under the nonlinear
    # GEOS projection). Cross-check against gw.lonlat_to_pixels (independently
    # validated in test_geolocation.py) applied directly to all 4 corners, rather than
    # assuming the box equals the two landmarks' own bounding box.
    min_lon, max_lon = min(DENVER[0], PHOENIX[0]), max(DENVER[0], PHOENIX[0])
    min_lat, max_lat = min(DENVER[1], PHOENIX[1]), max(DENVER[1], PHOENIX[1])
    box = gw.lonlat_box_to_crop_fraction("GOES18", "CONUS", None, IMG_W, IMG_H, min_lon, min_lat, max_lon, max_lat)
    assert box is not None
    left, top, right, bottom = box

    corner_lons = np.array([min_lon, max_lon, max_lon, min_lon])
    corner_lats = np.array([min_lat, min_lat, max_lat, max_lat])
    cols, rows = gw.lonlat_to_pixels("GOES18", corner_lons, corner_lats, IMG_W, IMG_H)
    clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731 -- matches the function's own clamping
    assert left == pytest.approx(clamp(cols.min() / IMG_W), abs=1e-6)
    assert right == pytest.approx(clamp(cols.max() / IMG_W), abs=1e-6)
    assert top == pytest.approx(clamp(rows.min() / IMG_H), abs=1e-6)
    assert bottom == pytest.approx(clamp(rows.max() / IMG_H), abs=1e-6)


def test_result_is_a_valid_crop_fractional_input():
    box = gw.lonlat_box_to_crop_fraction("GOES18", "CONUS", None, IMG_W, IMG_H, -112.07, 33.45, -104.99, 39.74)
    assert box is not None
    left, top, right, bottom = box
    assert 0.0 <= left < right <= 1.0
    assert 0.0 <= top < bottom <= 1.0


def test_full_disk_sector_is_supported():
    # GOES18/FD subpoint at (-137.0, 0.0) -> (2712.0, 2712.0) at 5424x5424 (see
    # test_geolocation.FULL_DISK_LANDMARKS).
    box = gw.lonlat_box_to_crop_fraction("GOES18", "FD", None, 5424, 5424, -140.0, -3.0, -134.0, 3.0)
    assert box is not None
    left, top, right, bottom = box
    assert left < 2712.0 / 5424 < right
    assert top < 2712.0 / 5424 < bottom


def test_uncalibrated_satellite_returns_none():
    assert gw.lonlat_box_to_crop_fraction("GOES16", "CONUS", None, IMG_W, IMG_H, -112.0, 33.0, -105.0, 40.0) is None


def test_mesoscale_sector_returns_none():
    assert gw.lonlat_box_to_crop_fraction("GOES18", "M1", None, 1000, 1000, -112.0, 33.0, -105.0, 40.0) is None


def test_uses_real_area_info_when_given():
    # An AreaInfo equivalent to GOES18/CONUS (see test_geolocation's
    # test_lonlat_to_pixels_area_matches_lonlat_to_pixels_for_equivalent_area) should
    # reproduce the same result as the hand-calibrated CONUS lookup.
    area = gw.AreaInfo(
        proj4_params={
            "proj": "geos", "sweep": "x", "lon_0": -137.0, "h": 35786023,
            "x_0": 0, "y_0": 0, "ellps": "GRS80", "units": "m",
        },
        extent=(-2505021.61, 1583173.65752, 2505021.61, 4589199.58952),
    )
    via_area = gw.lonlat_box_to_crop_fraction(
        "GOES18", "CONUS", area, IMG_W, IMG_H, -112.07, 33.45, -104.99, 39.74,
    )
    via_calibration = gw.lonlat_box_to_crop_fraction(
        "GOES18", "CONUS", None, IMG_W, IMG_H, -112.07, 33.45, -104.99, 39.74,
    )
    assert via_area == pytest.approx(via_calibration, abs=1e-6)


def test_area_info_supports_any_sector_including_mesoscale():
    # Unlike the hand-calibrated lookup (Mesoscale unsupported), a real per-frame
    # AreaInfo works regardless of `sector` -- reuse the CONUS-equivalent area above
    # but pass sector="M1" to prove the sector string itself is irrelevant once a
    # real `area` is given.
    area = gw.AreaInfo(
        proj4_params={
            "proj": "geos", "sweep": "x", "lon_0": -137.0, "h": 35786023,
            "x_0": 0, "y_0": 0, "ellps": "GRS80", "units": "m",
        },
        extent=(-2505021.61, 1583173.65752, 2505021.61, 4589199.58952),
    )
    box = gw.lonlat_box_to_crop_fraction("GOES18", "M1", area, IMG_W, IMG_H, -112.07, 33.45, -104.99, 39.74)
    assert box is not None


def test_result_clamped_to_frame_bounds():
    # A box mostly outside the CONUS frame should still return a valid, clamped crop
    # rather than a fraction outside [0, 1].
    box = gw.lonlat_box_to_crop_fraction("GOES18", "CONUS", None, IMG_W, IMG_H, -180.0, -10.0, -104.99, 39.74)
    assert box is not None
    for value in box:
        assert 0.0 <= value <= 1.0
