# tests/test_overlay_shell.py -- shell-out GeoJSON overlay provider
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers fetch_shell_geojson (runs an external command, parses stdout as GeoJSON,
never raises) and draw_geojson_overlay (projects/draws whatever features it returns).
Uses `sys.executable -c "..."` as the external command so these tests don't depend on
any script existing on disk or on real network access."""

import json
import sys

import numpy as np
import pytest
from PIL import Image

import goes_wallpaper as gw


def _print_json_command(payload: dict) -> tuple[str, ...]:
    return (sys.executable, "-c", f"import json; print(json.dumps({payload!r}))")


class TestFetchShellGeojson:
    def test_empty_command_returns_none(self):
        assert gw.fetch_shell_geojson((), 10.0) is None

    def test_parses_stdout_as_geojson(self):
        payload = {"type": "FeatureCollection", "features": []}
        result = gw.fetch_shell_geojson(_print_json_command(payload), 10.0)
        assert result == payload

    def test_nonzero_exit_returns_none(self):
        cmd = (sys.executable, "-c", "import sys; sys.exit(1)")
        assert gw.fetch_shell_geojson(cmd, 10.0) is None

    def test_invalid_json_returns_none(self):
        cmd = (sys.executable, "-c", "print('not json')")
        assert gw.fetch_shell_geojson(cmd, 10.0) is None

    def test_missing_executable_returns_none(self):
        assert gw.fetch_shell_geojson(("definitely-not-a-real-command-xyz",), 10.0) is None

    def test_timeout_returns_none(self):
        cmd = (sys.executable, "-c", "import time; time.sleep(5)")
        assert gw.fetch_shell_geojson(cmd, 0.1) is None


class TestIterGeojsonFeatures:
    def test_feature_collection(self):
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {}, "properties": {}}]}
        assert gw._iter_geojson_features(fc) == fc["features"]

    def test_single_feature(self):
        feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}}
        assert gw._iter_geojson_features(feature) == [feature]

    def test_bare_geometry_gets_wrapped(self):
        geometry = {"type": "Point", "coordinates": [1, 2]}
        features = gw._iter_geojson_features(geometry)
        assert len(features) == 1
        assert features[0]["geometry"] == geometry
        assert features[0]["properties"] == {}

    def test_empty_dict_returns_no_features(self):
        assert gw._iter_geojson_features({}) == []


class TestResolveFeatureColor:
    DEFAULT = (10, 20, 30)

    @pytest.mark.parametrize("missing_value", [None, "", [], 0, False])
    def test_falsy_values_use_default(self, missing_value):
        assert gw._resolve_feature_color(missing_value, self.DEFAULT) == self.DEFAULT

    def test_rgb_list(self):
        assert gw._resolve_feature_color([1, 2, 3], self.DEFAULT) == (1, 2, 3)

    def test_rgb_tuple(self):
        assert gw._resolve_feature_color((1, 2, 3), self.DEFAULT) == (1, 2, 3)

    def test_rgba_list_ignores_alpha(self):
        assert gw._resolve_feature_color([1, 2, 3, 255], self.DEFAULT) == (1, 2, 3)

    def test_named_color(self):
        assert gw._resolve_feature_color("red", self.DEFAULT) == (255, 0, 0)

    def test_hex_color(self):
        assert gw._resolve_feature_color("#00ff00", self.DEFAULT) == (0, 255, 0)

    def test_unparseable_string_uses_default(self):
        assert gw._resolve_feature_color("not-a-real-color", self.DEFAULT) == self.DEFAULT

    def test_wrong_shape_list_uses_default(self):
        assert gw._resolve_feature_color([1, 2], self.DEFAULT) == self.DEFAULT

    def test_unexpected_type_uses_default(self):
        assert gw._resolve_feature_color(3.14, self.DEFAULT) == self.DEFAULT


class TestDrawGeojsonOverlay:
    def _blank(self):
        return Image.new("RGB", (2500, 1500), (0, 0, 0))

    def _nonblack_pixel_count(self, img):
        return int((np.array(img).sum(axis=2) > 0).sum())

    def test_no_features_returns_image_unchanged(self):
        img = self._blank()
        out = gw.draw_geojson_overlay(img, "GOES18", {"type": "FeatureCollection", "features": []}, (255, 0, 0), 2, 5, 200)
        assert self._nonblack_pixel_count(out) == 0

    def test_point_feature_draws_something(self):
        img = self._blank()
        geojson = {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},  # SF, well within a GOES18 CONUS frame
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (255, 0, 0), 2, 5, 200)
        assert self._nonblack_pixel_count(out) > 0

    def test_linestring_feature_draws_something(self):
        img = self._blank()
        geojson = {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "LineString", "coordinates": [[-122.42, 37.77], [-118.24, 34.05]]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (255, 0, 0), 2, 5, 200)
        assert self._nonblack_pixel_count(out) > 0

    def test_closed_polygon_ring_is_fully_connected(self):
        # A small box fully inside the frame -- every edge (including the closing one
        # back to the first point) should render, not just an open polyline.
        img = self._blank()
        geojson = {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-108, 40], [-107, 40], [-107, 39], [-108, 39]]],
            },
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 200, 255), 2, 5, 200)
        arr = np.array(out)
        ys, xs = np.where(arr.sum(axis=2) > 0)
        col0, row0 = gw.lonlat_to_pixels("GOES18", np.array([-108.0]), np.array([40.0]), 2500, 1500)
        col2, row2 = gw.lonlat_to_pixels("GOES18", np.array([-107.0]), np.array([39.0]), 2500, 1500)
        # the drawn extent should span (roughly) the full box, not stop partway around it
        assert xs.min() == pytest.approx(min(col0[0], col2[0]), abs=3)
        assert xs.max() == pytest.approx(max(col0[0], col2[0]), abs=3)

    def test_property_color_overrides_default_color(self):
        img = self._blank()
        default_color = (255, 0, 0)
        override_color = (0, 255, 0)
        geojson = {
            "type": "Feature",
            "properties": {"color": list(override_color)},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        # opacity=255 (fully opaque) so the composited pixel color exactly matches the
        # fill color, instead of a partially-blended-with-black value.
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, default_color, 2, 5, 255)
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert override_color in drawn_colors
        assert default_color not in drawn_colors

    @pytest.mark.parametrize("color_value, expected_rgb", [
        ("red", (255, 0, 0)),
        ("#00ff00", (0, 255, 0)),
        ("blue", (0, 0, 255)),
    ])
    def test_property_color_accepts_named_and_hex_strings(self, color_value, expected_rgb):
        img = self._blank()
        geojson = {
            "type": "Feature",
            "properties": {"color": color_value},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (128, 128, 128), 2, 5, 255)
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert expected_rgb in drawn_colors

    def test_unparseable_property_color_falls_back_to_default_without_raising(self):
        img = self._blank()
        default_color = (128, 128, 128)
        geojson = {
            "type": "Feature",
            "properties": {"color": "not-a-real-color"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, default_color, 2, 5, 255)  # must not raise
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert default_color in drawn_colors

    def test_point_with_name_draws_more_than_point_without_name(self):
        # No real font file needed -- an empty font_path deliberately fails
        # ImageFont.truetype and falls back to ImageFont.load_default(), same as a
        # missing/invalid configured font would in production.
        point = {"type": "Point", "coordinates": [-122.42, 37.77]}
        unlabeled = gw.draw_geojson_overlay(
            self._blank(), "GOES18", {"type": "Feature", "properties": {}, "geometry": point}, (255, 0, 0), 2, 5, 255,
        )
        labeled = gw.draw_geojson_overlay(
            self._blank(), "GOES18",
            {"type": "Feature", "properties": {"name": "San Francisco"}, "geometry": point},
            (255, 0, 0), 2, 5, 255,
        )
        assert self._nonblack_pixel_count(labeled) > self._nonblack_pixel_count(unlabeled)

    def test_multipoint_draws_label_at_every_point(self):
        geojson = {
            "type": "Feature",
            "properties": {"name": "dup"},
            "geometry": {"type": "MultiPoint", "coordinates": [[-122.42, 37.77], [-118.24, 34.05]]},
        }
        out = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 5, 255)
        arr = np.array(out)
        ys, xs = np.where(arr.sum(axis=2) > 0)
        # SF and LA project to very different columns -- if only one label were drawn,
        # the nonblack extent would cluster around a single point instead of spanning both.
        assert xs.max() - xs.min() > 100

    def test_missing_font_path_falls_back_without_raising(self):
        geojson = {
            "type": "Feature", "properties": {"name": "SF"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(
            self._blank(), "GOES18", geojson, (255, 0, 0), 2, 5, 255, "not-a-real-font.ttf", 14,
        )
        assert self._nonblack_pixel_count(out) > 0

    def test_linestring_ignores_name_property(self):
        # name only makes sense for point markers -- a line/polygon feature carrying
        # one shouldn't attempt to draw a label (there's no single anchor point for it).
        geojson = {
            "type": "Feature", "properties": {"name": "should be ignored"},
            "geometry": {"type": "LineString", "coordinates": [[-122.42, 37.77], [-118.24, 34.05]]},
        }
        with_name = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 5, 255)
        geojson["properties"] = {}
        without_name = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 5, 255)
        assert self._nonblack_pixel_count(with_name) == self._nonblack_pixel_count(without_name)

    def test_unsupported_satellite_leaves_image_unchanged(self):
        img = self._blank()
        geojson = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}}
        out = gw.draw_geojson_overlay(img, "GOES16", geojson, (255, 0, 0), 2, 5, 200)
        assert self._nonblack_pixel_count(out) == 0

    def test_end_to_end_shell_command_to_render(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            ],
        }
        geojson = gw.fetch_shell_geojson(_print_json_command(payload), 10.0)
        assert geojson is not None
        out = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 5, 200)
        assert self._nonblack_pixel_count(out) > 0


class TestDrawOverlaysWiring:
    """Covers overlay_shell_command specifically through draw_overlays() -- the other
    functions above call fetch_shell_geojson/draw_geojson_overlay directly, which
    proves the pieces work but not that draw_overlays actually wires cfg.
    overlay_shell_command/overlay_shell_* through to them, or that a broken command
    is isolated the way overlay_geojson_files already is (see
    tests/test_overlay_geojson_files.py::TestDrawOverlaysWiring)."""

    def _blank(self):
        return Image.new("RGB", (2500, 1500), (0, 0, 0))

    def _nonblack_pixel_count(self, img):
        return int((np.array(img).sum(axis=2) > 0).sum())

    def _source(self):
        return gw.resolve_source(gw.Config(satellite="GOES18", sector="CONUS"), None)

    def test_overlay_shell_command_alone_triggers_draw_overlays(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            ],
        }
        cfg = gw.Config(overlay_shell_command=_print_json_command(payload))
        out = gw.draw_overlays(self._blank(), cfg, self._source())
        assert self._nonblack_pixel_count(out) > 0

    def test_nonzero_exit_command_does_not_crash_draw_overlays(self):
        cfg = gw.Config(overlay_shell_command=(sys.executable, "-c", "import sys; sys.exit(1)"))
        out = gw.draw_overlays(self._blank(), cfg, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) == 0

    def test_invalid_json_command_does_not_crash_draw_overlays(self):
        cfg = gw.Config(overlay_shell_command=(sys.executable, "-c", "print('not json')"))
        out = gw.draw_overlays(self._blank(), cfg, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) == 0

    def test_command_returning_non_geojson_json_does_not_crash_draw_overlays(self):
        # valid JSON, but not GeoJSON-shaped (no recognizable "type") -- exercises the
        # try/except around draw_geojson_overlay itself, not just fetch_shell_geojson.
        cfg = gw.Config(overlay_shell_command=_print_json_command({"unrelated": "payload"}))
        out = gw.draw_overlays(self._blank(), cfg, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) == 0
