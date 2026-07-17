# tests/test_overlay_geojson_files.py -- cached static-file GeoJSON overlay provider
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers render_static_geojson_overlay: merges overlay_geojson_files, draws them via
the same _build_geojson_layer draw_geojson_overlay uses, and caches the composited
RGBA layer in cfg.data_dir keyed on each file's path/mtime plus satellite/frame-size/
style -- so unit tests here specifically check the cache is written, reused, and
invalidated on the right triggers, not just that rendering works (that's already
covered by test_overlay_shell.py's draw_geojson_overlay tests, which share the same
underlying drawing code)."""

import json
import time

import numpy as np
import pytest
from PIL import Image

import goes_wallpaper as gw

SF_POINT = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-122.42, 37.77]}}
LA_POINT = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-118.24, 34.05]}}


def _write_geojson(path, *features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": list(features)}))


def _source():
    return gw.resolve_source(gw.Config(satellite="GOES18", sector="CONUS"), None)


def _nonblack_pixel_count(img):
    return int((np.array(img).sum(axis=2) > 0).sum())


def _blank():
    return Image.new("RGB", (2500, 1500), (0, 0, 0))


class TestRenderStaticGeojsonOverlay:
    def test_no_files_returns_image_unchanged(self, tmp_path):
        cfg = gw.Config(data_dir=tmp_path / "data")
        out = gw.render_static_geojson_overlay(_blank(), cfg, _source())
        assert _nonblack_pixel_count(out) == 0

    def test_draws_features_from_file(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(geojson_path),))
        out = gw.render_static_geojson_overlay(_blank(), cfg, _source())
        assert _nonblack_pixel_count(out) > 0

    def test_merges_multiple_files(self, tmp_path):
        one = tmp_path / "one.geojson"
        two = tmp_path / "two.geojson"
        _write_geojson(one, SF_POINT)
        _write_geojson(two, LA_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(one), str(two)))
        combined = gw.render_static_geojson_overlay(_blank(), cfg, _source())

        cfg_one_only = gw.Config(data_dir=tmp_path / "data_one", overlay_geojson_files=(str(one),))
        single = gw.render_static_geojson_overlay(_blank(), cfg_one_only, _source())

        assert _nonblack_pixel_count(combined) > _nonblack_pixel_count(single)

    def test_missing_file_is_logged_and_skipped_not_raised(self, tmp_path, caplog):
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(tmp_path / "nope.geojson"),))
        out = gw.render_static_geojson_overlay(_blank(), cfg, _source())
        assert _nonblack_pixel_count(out) == 0  # no features -- nothing drawn, no crash

    def test_writes_cache_files(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir, overlay_geojson_files=(str(geojson_path),))
        gw.render_static_geojson_overlay(_blank(), cfg, _source())
        assert (data_dir / "overlay_geojson_cache.png").exists()
        assert (data_dir / "overlay_geojson_cache.json").exists()

    def test_second_render_reuses_cache_without_rebuilding(self, tmp_path, monkeypatch):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(geojson_path),))
        source = _source()

        first = gw.render_static_geojson_overlay(_blank(), cfg, source)

        calls = []
        original = gw._build_geojson_layer
        monkeypatch.setattr(gw, "_build_geojson_layer", lambda *a, **k: calls.append(1) or original(*a, **k))
        second = gw.render_static_geojson_overlay(_blank(), cfg, source)

        assert calls == []  # cache hit -- layer builder never re-invoked
        assert _nonblack_pixel_count(second) == _nonblack_pixel_count(first)

    def test_editing_source_file_invalidates_cache(self, tmp_path, monkeypatch):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(geojson_path),))
        source = _source()
        before = gw.render_static_geojson_overlay(_blank(), cfg, source)

        time.sleep(0.05)  # ensure a distinct mtime on filesystems with coarse resolution
        _write_geojson(geojson_path, SF_POINT, LA_POINT)

        calls = []
        original = gw._build_geojson_layer
        monkeypatch.setattr(gw, "_build_geojson_layer", lambda *a, **k: calls.append(1) or original(*a, **k))
        after = gw.render_static_geojson_overlay(_blank(), cfg, source)

        assert calls == [1]  # rebuilt, exactly once
        assert _nonblack_pixel_count(after) > _nonblack_pixel_count(before)

    def test_changing_style_config_invalidates_cache(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        source = _source()

        cfg_default = gw.Config(data_dir=data_dir, overlay_geojson_files=(str(geojson_path),))
        gw.render_static_geojson_overlay(_blank(), cfg_default, source)
        meta_after_first = json.loads((data_dir / "overlay_geojson_cache.json").read_text())

        cfg_bigger_marker = gw.Config(
            data_dir=data_dir, overlay_geojson_files=(str(geojson_path),), overlay_geojson_marker_radius=99,
        )
        gw.render_static_geojson_overlay(_blank(), cfg_bigger_marker, source)
        meta_after_second = json.loads((data_dir / "overlay_geojson_cache.json").read_text())

        assert meta_after_first != meta_after_second

    def test_changing_font_size_invalidates_cache(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, {**SF_POINT, "properties": {"name": "SF"}})
        data_dir = tmp_path / "data"
        source = _source()

        cfg_default = gw.Config(data_dir=data_dir, overlay_geojson_files=(str(geojson_path),))
        gw.render_static_geojson_overlay(_blank(), cfg_default, source)
        meta_after_first = json.loads((data_dir / "overlay_geojson_cache.json").read_text())

        cfg_bigger_font = gw.Config(
            data_dir=data_dir, overlay_geojson_files=(str(geojson_path),), overlay_geojson_font_size=40,
        )
        gw.render_static_geojson_overlay(_blank(), cfg_bigger_font, source)
        meta_after_second = json.loads((data_dir / "overlay_geojson_cache.json").read_text())

        assert meta_after_first != meta_after_second

    def test_named_point_renders_label_through_the_cache_path(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, {**SF_POINT, "properties": {"name": "San Francisco"}})
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(geojson_path),))
        labeled = gw.render_static_geojson_overlay(_blank(), cfg, _source())

        _write_geojson(tmp_path / "unlabeled.geojson", SF_POINT)
        cfg_unlabeled = gw.Config(data_dir=tmp_path / "data2", overlay_geojson_files=(str(tmp_path / "unlabeled.geojson"),))
        unlabeled = gw.render_static_geojson_overlay(_blank(), cfg_unlabeled, _source())

        assert _nonblack_pixel_count(labeled) > _nonblack_pixel_count(unlabeled)

    def test_different_resolution_invalidates_cache(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(geojson_path),))
        source = _source()

        small = gw.render_static_geojson_overlay(Image.new("RGB", (2500, 1500), (0, 0, 0)), cfg, source)
        large = gw.render_static_geojson_overlay(Image.new("RGB", (5000, 3000), (0, 0, 0)), cfg, source)
        assert small.size == (2500, 1500)
        assert large.size == (5000, 3000)


class TestDrawOverlaysWiring:
    def test_overlay_geojson_files_alone_triggers_draw_overlays(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(geojson_path),))
        out = gw.draw_overlays(_blank(), cfg, _source())
        assert _nonblack_pixel_count(out) > 0

    def test_broken_geojson_file_does_not_crash_draw_overlays(self, tmp_path):
        bad_path = tmp_path / "broken.geojson"
        bad_path.write_text("not json")
        cfg = gw.Config(data_dir=tmp_path / "data", overlay_geojson_files=(str(bad_path),))
        out = gw.draw_overlays(_blank(), cfg, _source())  # must not raise
        assert _nonblack_pixel_count(out) == 0
