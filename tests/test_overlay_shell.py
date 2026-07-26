# tests/test_overlay_shell.py -- shell-out GeoJSON overlay provider
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers fetch_shell_geojson (runs an external command, parses stdout as GeoJSON,
never raises) and draw_geojson_overlay (projects/draws whatever features it returns).
Uses `sys.executable -c "..."` as the external command so these tests don't depend on
any script existing on disk or on real network access."""

import json
import sys
from pathlib import Path

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


class TestResolveFillColor:
    def test_no_property_falls_back_to_entry_default(self):
        assert gw._resolve_fill_color(None, (1, 2, 3)) == (1, 2, 3)

    def test_no_property_and_no_entry_default_stays_none(self):
        assert gw._resolve_fill_color(None, None) is None

    def test_property_fill_overrides_entry_default(self):
        assert gw._resolve_fill_color([9, 8, 7], (1, 2, 3)) == (9, 8, 7)

    def test_property_fill_opts_in_even_with_no_entry_default(self):
        assert gw._resolve_fill_color("red", None) == (255, 0, 0)

    def test_unparseable_property_fill_with_no_entry_default_falls_back_to_white(self):
        # The feature explicitly asked for *some* fill; falling all the way back to
        # "no fill" over an unparseable value would silently drop that intent.
        assert gw._resolve_fill_color("not-a-real-color", None) == (255, 255, 255)


class TestResolveOpacity:
    def test_none_uses_default(self):
        assert gw._resolve_opacity(None, 160) == 160

    @pytest.mark.parametrize("value, expected", [(0.0, 0), (1.0, 255), (0.5, 128)])
    def test_valid_range_converts_to_0_255(self, value, expected):
        assert gw._resolve_opacity(value, 160) == expected

    def test_string_number_is_accepted(self):
        assert gw._resolve_opacity("0.5", 160) == 128

    @pytest.mark.parametrize("value", [-0.1, 1.1, 5])
    def test_out_of_range_uses_default(self, value):
        assert gw._resolve_opacity(value, 160) == 160

    def test_unparseable_uses_default(self):
        assert gw._resolve_opacity("not-a-number", 160) == 160


class TestResolveMarkerSizeMultiplier:
    def test_none_is_1x(self):
        assert gw._resolve_marker_size_multiplier(None) == 1.0

    @pytest.mark.parametrize("size, multiplier", [("small", 0.6), ("medium", 1.0), ("large", 1.6)])
    def test_known_sizes(self, size, multiplier):
        assert gw._resolve_marker_size_multiplier(size) == multiplier

    def test_unrecognized_value_is_1x(self):
        assert gw._resolve_marker_size_multiplier("huge") == 1.0


class TestResolveIconPath:
    def test_none_or_empty_resolves_to_none(self):
        assert gw._resolve_icon_path(None) is None
        assert gw._resolve_icon_path("") is None

    def test_bundled_name_resolves_and_is_flagged_bundled(self):
        resolved = gw._resolve_icon_path("marker")
        assert resolved is not None
        path, is_bundled = resolved
        assert path.name == "marker.png"
        assert is_bundled is True

    def test_custom_path_resolves_and_is_not_flagged_bundled(self, tmp_path):
        icon_path = tmp_path / "custom.png"
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(icon_path)
        resolved = gw._resolve_icon_path(str(icon_path))
        assert resolved == (icon_path, False)

    def test_unrecognized_name_and_missing_path_resolves_to_none(self):
        assert gw._resolve_icon_path("definitely-not-a-bundled-icon-or-file") is None


class TestResolveFeatureIcon:
    def test_no_properties_falls_back_to_entry_icon(self):
        entry = (Path("entry.png"), False)
        assert gw._resolve_feature_icon({}, entry) == entry

    def test_no_properties_and_no_entry_icon_stays_none(self):
        assert gw._resolve_feature_icon({}, None) is None

    def test_properties_icon_bundled_name_overrides_entry(self):
        resolved = gw._resolve_feature_icon({"icon": "star"}, (Path("entry.png"), False))
        assert resolved is not None
        assert resolved[0].name == "star.png"
        assert resolved[1] is True

    def test_properties_marker_symbol_resolves_against_bundled_set(self):
        resolved = gw._resolve_feature_icon({"marker-symbol": "fire-station"}, None)
        assert resolved == (gw._bundled_icons()["fire-station"], True)

    def test_properties_icon_takes_precedence_over_marker_symbol(self):
        resolved = gw._resolve_feature_icon({"icon": "star", "marker-symbol": "fire-station"}, None)
        assert resolved[0].name == "star.png"

    def test_unrecognized_marker_symbol_is_ignored_falls_back_to_entry(self):
        entry = (Path("entry.png"), False)
        resolved = gw._resolve_feature_icon({"marker-symbol": "not-a-real-maki-icon"}, entry)
        assert resolved == entry


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

    def test_polygon_default_has_no_fill(self):
        # Unchanged default behavior: fill=None (never set) still means
        # outline-only, same as before fill support existed.
        img = self._blank()
        geojson = {
            "type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[-108, 40], [-107, 40], [-107, 39], [-108, 39]]]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 200, 255), 2, 5, 255)
        arr = np.array(out)
        # A filled square would light up a large contiguous interior; outline-only
        # should only light up a thin border -- well under 50% of the bbox area.
        ys, xs = np.where(arr.sum(axis=2) > 0)
        bbox_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
        assert len(xs) < 0.5 * bbox_area

    def test_polygon_with_fill_fills_the_interior(self):
        img = self._blank()
        geojson = {
            "type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[-108, 40], [-107, 40], [-107, 39], [-108, 39]]]},
        }
        out = gw.draw_geojson_overlay(
            img, "GOES18", geojson, (0, 200, 255), 2, 5, 255, fill=(200, 0, 0), fill_opacity=255,
        )
        arr = np.array(out)
        ys, xs = np.where(arr.sum(axis=2) > 0)
        bbox_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
        # The projected quadrilateral is rotated/skewed relative to the pixel grid
        # (GEOS projection distortion), so even a fully solid fill doesn't cover
        # 100% of its own axis-aligned bounding box -- comfortably above the
        # outline-only case's <50% is enough to distinguish "filled" from "not".
        assert len(xs) > 0.55 * bbox_area

    def test_polygon_with_hole_leaves_the_hole_unfilled(self):
        img = self._blank()
        # A big outer square with a smaller inner square (opposite winding) as a hole.
        geojson = {
            "type": "Feature", "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-110, 42], [-104, 42], [-104, 36], [-110, 36]],  # exterior, CCW-ish in lon/lat
                    [[-108, 40], [-108, 38], [-106, 38], [-106, 40]],  # hole, opposite winding
                ],
            },
        }
        out = gw.draw_geojson_overlay(
            img, "GOES18", geojson, (0, 200, 255), 2, 5, 255, fill=(200, 0, 0), fill_opacity=255,
        )
        arr = np.array(out)
        hole_col, hole_row = gw.lonlat_to_pixels("GOES18", np.array([-107.0]), np.array([39.0]), 2500, 1500)
        outer_col, outer_row = gw.lonlat_to_pixels("GOES18", np.array([-109.0]), np.array([41.0]), 2500, 1500)
        assert tuple(arr[round(hole_row[0]), round(hole_col[0])]) == (0, 0, 0)  # inside the hole: untouched
        assert arr[round(outer_row[0]), round(outer_col[0])].sum() > 0  # in the annulus: filled

    def test_property_fill_overrides_entry_fill(self):
        img = self._blank()
        geojson = {
            "type": "Feature", "properties": {"fill": [0, 255, 0]},
            "geometry": {"type": "Polygon", "coordinates": [[[-108, 40], [-107, 40], [-107, 39], [-108, 39]]]},
        }
        out = gw.draw_geojson_overlay(
            img, "GOES18", geojson, (0, 200, 255), 2, 5, 255, fill=(200, 0, 0), fill_opacity=255,
        )
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert (0, 255, 0) in drawn_colors
        assert (200, 0, 0) not in drawn_colors

    def test_simplestyle_stroke_overrides_legacy_color(self):
        img = self._blank()
        geojson = {
            "type": "Feature", "properties": {"stroke": "lime", "color": "red"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 0, 255), 2, 5, 255)
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert (0, 255, 0) in drawn_colors  # "lime"
        assert (255, 0, 0) not in drawn_colors  # legacy "color" ignored when stroke is set

    def test_legacy_color_still_works_without_stroke(self):
        img = self._blank()
        geojson = {
            "type": "Feature", "properties": {"color": "red"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 0, 255), 2, 5, 255)
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert (255, 0, 0) in drawn_colors

    def test_marker_color_takes_precedence_over_stroke_for_points(self):
        img = self._blank()
        geojson = {
            "type": "Feature", "properties": {"marker-color": "lime", "stroke": "red"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 0, 255), 2, 5, 255)
        drawn_colors = {tuple(c) for c in np.array(out)[np.array(out).sum(axis=2) > 0].tolist()}
        assert (0, 255, 0) in drawn_colors

    def test_marker_size_large_draws_more_than_small(self):
        small = gw.draw_geojson_overlay(
            self._blank(), "GOES18",
            {"type": "Feature", "properties": {"marker-size": "small"}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            (255, 0, 0), 2, 5, 255,
        )
        large = gw.draw_geojson_overlay(
            self._blank(), "GOES18",
            {"type": "Feature", "properties": {"marker-size": "large"}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            (255, 0, 0), 2, 5, 255,
        )
        assert self._nonblack_pixel_count(large) > self._nonblack_pixel_count(small)

    def test_stroke_opacity_is_a_0_to_1_float_not_0_to_255(self):
        opaque = gw.draw_geojson_overlay(
            self._blank(), "GOES18",
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            (255, 0, 0), 2, 5, 255,
        )
        faint = gw.draw_geojson_overlay(
            self._blank(), "GOES18",
            {"type": "Feature", "properties": {"stroke-opacity": 0.1}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            (255, 0, 0), 2, 5, 255,
        )
        col, row = gw.lonlat_to_pixels("GOES18", np.array([-122.42]), np.array([37.77]), 2500, 1500)
        # Sum intensity over a box around the marker circle (rather than one exact
        # pixel) so this isn't sensitive to which pixel the anti-aliased stroke
        # happens to land on.
        radius = round(5 * max(1.0, 2500 / 2000)) + 2
        c, r = round(col[0]), round(row[0])
        box = np.s_[r - radius:r + radius, c - radius:c + radius]
        assert np.array(opaque)[box].sum() > np.array(faint)[box].sum()

    def test_icon_replaces_the_outlined_circle(self):
        # Composite over red so we can tell "was an outlined circle stroke drawn"
        # (the base background color would remain pure red at the ring) apart from
        # "was an icon pasted" (the icon's own opaque pixels, tinted with the
        # marker color since it's a bundled icon).
        img = Image.new("RGB", (2500, 1500), (0, 0, 0))
        geojson = {
            "type": "Feature", "properties": {"icon": "marker"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        with_icon = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 255, 0), 2, 20, 255)
        geojson["properties"] = {}
        without_icon = gw.draw_geojson_overlay(img, "GOES18", geojson, (0, 255, 0), 2, 20, 255)
        # Both draw *something*, but a pasted icon (a filled silhouette) covers far
        # more pixels than a thin circle outline at the same radius.
        assert self._nonblack_pixel_count(with_icon) > self._nonblack_pixel_count(without_icon)

    def test_unrecognized_icon_falls_back_to_outlined_circle(self):
        geojson = {
            "type": "Feature", "properties": {"icon": "not-a-real-icon-or-path"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        out = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 5, 255)  # must not raise
        assert self._nonblack_pixel_count(out) > 0

    def test_marker_symbol_resolves_a_bundled_icon(self):
        geojson = {
            "type": "Feature", "properties": {"marker-symbol": "star"},
            "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]},
        }
        with_symbol = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 20, 255)
        geojson["properties"] = {}
        without_symbol = gw.draw_geojson_overlay(self._blank(), "GOES18", geojson, (255, 0, 0), 2, 20, 255)
        assert self._nonblack_pixel_count(with_symbol) > self._nonblack_pixel_count(without_symbol)

    def test_line_edge_has_partially_blended_alpha_not_just_hard_0_or_255(self):
        # Smoke check for anti-aliasing (aggdraw/AGG) replacing PIL ImageDraw's
        # hard-edged rasterization -- a diagonal line should have at least one
        # partially-covered edge pixel, not just fully-on/fully-off coverage.
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        draw = gw.aggdraw.Draw(img)
        pen = gw.aggdraw.Pen((255, 0, 0), 3, 255)
        draw.line((10, 10, 190, 150), pen)  # a non-axis-aligned diagonal
        draw.flush()
        alphas = np.array(img)[..., 3]
        distinct = np.unique(alphas)
        assert ((distinct > 0) & (distinct < 255)).any()

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
    """Covers shell_sources specifically through draw_overlays() -- the other
    functions above call fetch_shell_geojson/draw_geojson_overlay directly, which
    proves the pieces work but not that draw_overlays actually wires each
    ShellSource's command/style through to them, or that a broken command is
    isolated the way geojson_sources already is (see
    tests/test_overlay_geojson_files.py::TestDrawOverlaysWiring)."""

    def _blank(self):
        return Image.new("RGB", (2500, 1500), (0, 0, 0))

    def _nonblack_pixel_count(self, img):
        return int((np.array(img).sum(axis=2) > 0).sum())

    def _source(self):
        return gw.resolve_source(gw.Config(satellite="GOES18", sector="CONUS"), None)

    def test_shell_source_alone_triggers_draw_overlays(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            ],
        }
        cfg = gw.Config()
        overlays = gw.OverlaysConfig(shell_sources=(gw.ShellSource(name="storms", command=_print_json_command(payload)),))
        out = gw.draw_overlays(self._blank(), cfg, overlays, self._source())
        assert self._nonblack_pixel_count(out) > 0

    def test_nonzero_exit_command_does_not_crash_draw_overlays(self):
        cfg = gw.Config()
        command = (sys.executable, "-c", "import sys; sys.exit(1)")
        overlays = gw.OverlaysConfig(shell_sources=(gw.ShellSource(name="storms", command=command),))
        out = gw.draw_overlays(self._blank(), cfg, overlays, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) == 0

    def test_invalid_json_command_does_not_crash_draw_overlays(self):
        cfg = gw.Config()
        command = (sys.executable, "-c", "print('not json')")
        overlays = gw.OverlaysConfig(shell_sources=(gw.ShellSource(name="storms", command=command),))
        out = gw.draw_overlays(self._blank(), cfg, overlays, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) == 0

    def test_command_returning_non_geojson_json_does_not_crash_draw_overlays(self):
        # valid JSON, but not GeoJSON-shaped (no recognizable "type") -- exercises the
        # try/except around draw_geojson_overlay itself, not just fetch_shell_geojson.
        cfg = gw.Config()
        command = _print_json_command({"unrelated": "payload"})
        overlays = gw.OverlaysConfig(shell_sources=(gw.ShellSource(name="storms", command=command),))
        out = gw.draw_overlays(self._blank(), cfg, overlays, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) == 0

    def test_one_broken_shell_source_does_not_prevent_others_from_drawing(self):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}},
            ],
        }
        cfg = gw.Config()
        overlays = gw.OverlaysConfig(shell_sources=(
            gw.ShellSource(name="broken", command=(sys.executable, "-c", "import sys; sys.exit(1)")),
            gw.ShellSource(name="good", command=_print_json_command(payload)),
        ))
        out = gw.draw_overlays(self._blank(), cfg, overlays, self._source())  # must not raise
        assert self._nonblack_pixel_count(out) > 0
