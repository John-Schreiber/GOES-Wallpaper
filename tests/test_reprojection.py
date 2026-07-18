# tests/test_reprojection.py -- reproject_frame (output projection: platecarree/
# lambertconformal/orthographic/lambertazimuthal)
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers reproject_frame: warps a rendered frame from its native GEOS grid into a
different map projection via nearest-neighbor resampling (pure pyproj + numpy, no
pyresample/satpy). Each projection is cross-checked against an independent
computation (a direct linear formula for platecarree, pyproj's own forward transform
for the others) rather than re-deriving the function's own internals, and against
test_geolocation.KNOWN_LANDMARKS for the source-side calibration."""

import numpy as np
import pytest
from PIL import Image
from pyproj import CRS, Transformer

import goes_wallpaper as gw

SRC_W, SRC_H = 2500, 1500

# GOES18/CONUS landmark from test_geolocation.KNOWN_LANDMARKS, at 2500x1500.
DENVER_LON, DENVER_LAT = -104.99, 39.74
DENVER_COL, DENVER_ROW = 2464.57, 391.24


def _marker_image(col, row, size=(SRC_W, SRC_H)):
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    r, c = int(round(row)), int(round(col))
    arr[max(0, r - 2):r + 3, max(0, c - 2):c + 3] = 255
    return Image.fromarray(arr, "RGB")


def _marker_centroid(img):
    arr = np.asarray(img)
    ys, xs = np.where(arr[:, :, 0] > 200)
    assert len(xs) > 0, "marker not found in reprojected output"
    return float(xs.mean()), float(ys.mean())


class TestPlatecarree:
    def test_marker_lands_at_linear_formula_position(self):
        img = _marker_image(DENVER_COL, DENVER_ROW)
        bounds = (-115.0, 30.0, -95.0, 50.0)  # min_lon, min_lat, max_lon, max_lat
        out_w, out_h = 400, 400
        out = gw.reproject_frame(img, "GOES18", "CONUS", None, "platecarree", bounds, 0.0, 0.0, out_w, out_h)
        assert out is not None
        assert out.size == (out_w, out_h)

        cx, cy = _marker_centroid(out)
        expected_x = (DENVER_LON - bounds[0]) / (bounds[2] - bounds[0]) * out_w
        expected_y = (bounds[3] - DENVER_LAT) / (bounds[3] - bounds[1]) * out_h
        assert cx == pytest.approx(expected_x, abs=2)
        assert cy == pytest.approx(expected_y, abs=2)

    def test_uncalibrated_satellite_returns_none(self):
        img = Image.new("RGB", (SRC_W, SRC_H))
        out = gw.reproject_frame(img, "GOES16", "CONUS", None, "platecarree", (-115.0, 30.0, -95.0, 50.0), 0.0, 0.0, 400, 400)
        assert out is None

    def test_uses_real_area_info_when_given(self):
        area = gw.AreaInfo(
            proj4_params={
                "proj": "geos", "sweep": "x", "lon_0": -137.0, "h": 35786023,
                "x_0": 0, "y_0": 0, "ellps": "GRS80", "units": "m",
            },
            extent=(-2505021.61, 1583173.65752, 2505021.61, 4589199.58952),
        )
        img = _marker_image(DENVER_COL, DENVER_ROW)
        bounds = (-115.0, 30.0, -95.0, 50.0)
        via_area = gw.reproject_frame(img, "GOES18", "CONUS", area, "platecarree", bounds, 0.0, 0.0, 400, 400)
        via_calibration = gw.reproject_frame(img, "GOES18", "CONUS", None, "platecarree", bounds, 0.0, 0.0, 400, 400)
        assert np.array_equal(np.asarray(via_area), np.asarray(via_calibration))


class TestOrthographic:
    def test_marker_lands_at_pyproj_forward_transform_position(self):
        img = _marker_image(DENVER_COL, DENVER_ROW)
        out_w = out_h = 600
        out = gw.reproject_frame(img, "GOES18", "CONUS", None, "orthographic", None, -137.0, 0.0, out_w, out_h)
        assert out is not None
        assert out.size == (out_w, out_h)

        ortho = CRS.from_dict({"proj": "ortho", "lon_0": -137.0, "lat_0": 0.0, "ellps": "GRS80"})
        transformer = Transformer.from_crs("EPSG:4326", ortho, always_xy=True)
        x, y = transformer.transform(DENVER_LON, DENVER_LAT)
        r = gw._GRS80_SEMI_MAJOR_AXIS_M
        expected_x = (x - (-r)) / (2 * r) * out_w
        expected_y = (r - y) / (2 * r) * out_h

        cx, cy = _marker_centroid(out)
        assert cx == pytest.approx(expected_x, abs=2)
        assert cy == pytest.approx(expected_y, abs=2)

    def test_content_under_center_point_is_visible(self):
        # Center on Denver (which IS within GOES18's CONUS coverage) -- unlike the raw
        # satellite subpoint, which CONUS's real extent doesn't actually include (its
        # y-extent starts north of the equator).
        white = Image.new("RGB", (SRC_W, SRC_H), (255, 255, 255))
        out_w = out_h = 600
        out = gw.reproject_frame(white, "GOES18", "CONUS", None, "orthographic", None, DENVER_LON, DENVER_LAT, out_w, out_h)
        assert out is not None
        center = np.asarray(out)[out_h // 2, out_w // 2]
        assert tuple(center) == (255, 255, 255)

    def test_pixels_beyond_the_visible_hemisphere_are_black(self):
        # The 4 canvas corners are at radius sqrt(2)*R from center -- always beyond
        # the visible disk (radius R) regardless of source content.
        white = Image.new("RGB", (SRC_W, SRC_H), (255, 255, 255))
        out_w = out_h = 600
        out = np.asarray(gw.reproject_frame(white, "GOES18", "CONUS", None, "orthographic", None, DENVER_LON, DENVER_LAT, out_w, out_h))
        for corner in [out[0, 0], out[0, -1], out[-1, 0], out[-1, -1]]:
            assert tuple(corner) == (0, 0, 0)

    def test_uncalibrated_satellite_returns_none(self):
        img = Image.new("RGB", (SRC_W, SRC_H))
        out = gw.reproject_frame(img, "GOES16", "CONUS", None, "orthographic", None, -137.0, 0.0, 600, 600)
        assert out is None


class TestLambertConformal:
    def test_marker_lands_at_pyproj_forward_transform_position(self):
        img = _marker_image(DENVER_COL, DENVER_ROW)
        bounds = (-125.0, 25.0, -95.0, 50.0)
        out_w, out_h = 500, 350
        out = gw.reproject_frame(img, "GOES18", "CONUS", None, "lambertconformal", bounds, 0.0, 0.0, out_w, out_h)
        assert out is not None
        assert out.size == (out_w, out_h)

        min_lon, min_lat, max_lon, max_lat = bounds
        lon_0, lat_0 = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
        span = max_lat - min_lat
        lat_1, lat_2 = min_lat + span / 6, max_lat - span / 6
        lcc = CRS.from_dict({"proj": "lcc", "lon_0": lon_0, "lat_0": lat_0, "lat_1": lat_1, "lat_2": lat_2, "ellps": "GRS80"})
        x0, y0, x1, y1 = gw._bounds_projected_extent(lcc, min_lon, min_lat, max_lon, max_lat)
        transformer = Transformer.from_crs("EPSG:4326", lcc, always_xy=True)
        x, y = transformer.transform(DENVER_LON, DENVER_LAT)
        expected_x = (x - x0) / (x1 - x0) * out_w
        expected_y = (y1 - y) / (y1 - y0) * out_h

        cx, cy = _marker_centroid(out)
        assert cx == pytest.approx(expected_x, abs=2)
        assert cy == pytest.approx(expected_y, abs=2)

    def test_explicit_standard_parallels_change_the_result(self):
        # A small marker can be stepped over entirely by nearest-neighbor
        # destination-to-source sampling at this canvas resolution (same class of
        # artifact as TestLambertAzimuthal's tests below) -- use an all-white source
        # and compare the resulting valid-pixel masks instead, which differ because
        # different standard parallels project the same lon/lat bounds to a
        # differently-shaped/sized extent.
        white = Image.new("RGB", (SRC_W, SRC_H), (255, 255, 255))
        bounds = (-125.0, 25.0, -95.0, 50.0)
        default = gw.reproject_frame(white, "GOES18", "CONUS", None, "lambertconformal", bounds, 0.0, 0.0, 300, 200)
        overridden = gw.reproject_frame(
            white, "GOES18", "CONUS", None, "lambertconformal", bounds, 0.0, 0.0, 300, 200, lcc_lat1=30.0, lcc_lat2=45.0,
        )
        assert not np.array_equal(np.asarray(default), np.asarray(overridden))

    def test_uncalibrated_satellite_returns_none(self):
        img = Image.new("RGB", (SRC_W, SRC_H))
        out = gw.reproject_frame(img, "GOES16", "CONUS", None, "lambertconformal", (-125.0, 25.0, -95.0, 50.0), 0.0, 0.0, 300, 200)
        assert out is None


class TestLambertAzimuthal:
    def test_content_under_center_point_is_visible(self):
        # A marker much smaller than one output pixel's ground footprint (the whole
        # globe over a modest canvas) can be stepped over entirely by nearest-neighbor
        # destination-to-source sampling -- use an all-white source instead of a small
        # marker, matching the equivalent orthographic test above.
        white = Image.new("RGB", (SRC_W, SRC_H), (255, 255, 255))
        out_w = out_h = 600
        out = gw.reproject_frame(white, "GOES18", "CONUS", None, "lambertazimuthal", None, DENVER_LON, DENVER_LAT, out_w, out_h)
        assert out is not None
        center = np.asarray(out)[out_h // 2, out_w // 2]
        assert tuple(center) == (255, 255, 255)

    def test_pixels_near_the_antipode_are_black(self):
        white = Image.new("RGB", (SRC_W, SRC_H), (255, 255, 255))
        out_w = out_h = 600
        out = np.asarray(gw.reproject_frame(white, "GOES18", "CONUS", None, "lambertazimuthal", None, DENVER_LON, DENVER_LAT, out_w, out_h))
        for corner in [out[0, 0], out[0, -1], out[-1, 0], out[-1, -1]]:
            assert tuple(corner) == (0, 0, 0)

    def test_extent_reaches_beyond_orthographic_max_radius(self):
        # lambertazimuthal is valid out to (not including) the antipode, radius 2R --
        # orthographic caps out at radius R (the visible-hemisphere limit). This is a
        # projection-math property, not something a rendered pixel count can show:
        # lambertazimuthal's canvas physically covers *more area* per pixel to fit the
        # whole globe in the same pixel budget, so the same real (small) source region
        # actually covers *fewer* pixels there than in orthographic's more tightly
        # zoomed hemisphere-only canvas -- "shows more of the globe" is about
        # reachable coverage, not pixel count, so verify that directly instead.
        laea = CRS.from_dict({"proj": "laea", "lon_0": DENVER_LON, "lat_0": DENVER_LAT, "ellps": "GRS80"})
        inverse = Transformer.from_crs(laea, "EPSG:4326", always_xy=True)
        r_ortho = gw._GRS80_SEMI_MAJOR_AXIS_M
        beyond_ortho_radius = 1.5 * r_ortho
        lon, lat = inverse.transform(beyond_ortho_radius, 0.0)
        assert np.isfinite(lon) and np.isfinite(lat)

    def test_uncalibrated_satellite_returns_none(self):
        img = Image.new("RGB", (SRC_W, SRC_H))
        out = gw.reproject_frame(img, "GOES16", "CONUS", None, "lambertazimuthal", None, -137.0, 0.0, 600, 600)
        assert out is None


def test_unknown_projection_raises():
    img = Image.new("RGB", (SRC_W, SRC_H))
    with pytest.raises(ValueError, match="unknown projection"):
        gw.reproject_frame(img, "GOES18", "CONUS", None, "bogus", None, 0.0, 0.0, 100, 100)


class TestSatelliteLon0:
    def test_hand_calibrated_satellite(self):
        assert gw._satellite_lon_0("GOES18", "CONUS", None) == -137.0
        assert gw._satellite_lon_0("GOES19", "FD", None) == -75.0

    def test_uncalibrated_returns_none(self):
        assert gw._satellite_lon_0("GOES16", "CONUS", None) is None
        assert gw._satellite_lon_0("GOES18", "M1", None) is None

    def test_area_info_takes_precedence(self):
        area = gw.AreaInfo(proj4_params={"lon_0": -99.9}, extent=(0, 0, 1, 1))
        assert gw._satellite_lon_0("GOES18", "CONUS", area) == -99.9
