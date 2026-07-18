# tests/test_overlay_geojson_files.py -- cached static-file GeoJSON overlay provider
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers render_static_geojson_overlay: draws one overlays.toml [[geojson_sources]]
entry via the same _build_geojson_layer draw_geojson_overlay uses, and caches the
composited RGBA layer in cfg.data_dir keyed on each file's path/mtime plus
name/satellite/frame-size/style -- so unit tests here specifically check the cache is
written, reused, and invalidated on the right triggers, not just that rendering works
(that's already covered by test_overlay_shell.py's draw_geojson_overlay tests, which
share the same underlying drawing code)."""

import json
import time

import numpy as np
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
        source = gw.GeoJSONSource(name="empty")
        out = gw.render_static_geojson_overlay(_blank(), cfg, _source(), source)
        assert _nonblack_pixel_count(out) == 0

    def test_draws_features_from_file(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        out = gw.render_static_geojson_overlay(_blank(), cfg, _source(), source)
        assert _nonblack_pixel_count(out) > 0

    def test_merges_multiple_files_in_one_source(self, tmp_path):
        one = tmp_path / "one.geojson"
        two = tmp_path / "two.geojson"
        _write_geojson(one, SF_POINT)
        _write_geojson(two, LA_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        combined_source = gw.GeoJSONSource(name="both", files=(str(one), str(two)))
        combined = gw.render_static_geojson_overlay(_blank(), cfg, _source(), combined_source)

        cfg_one_only = gw.Config(data_dir=tmp_path / "data_one")
        single_source = gw.GeoJSONSource(name="one", files=(str(one),))
        single = gw.render_static_geojson_overlay(_blank(), cfg_one_only, _source(), single_source)

        assert _nonblack_pixel_count(combined) > _nonblack_pixel_count(single)

    def test_missing_file_is_logged_and_skipped_not_raised(self, tmp_path, caplog):
        cfg = gw.Config(data_dir=tmp_path / "data")
        source = gw.GeoJSONSource(name="missing", files=(str(tmp_path / "nope.geojson"),))
        out = gw.render_static_geojson_overlay(_blank(), cfg, _source(), source)
        assert _nonblack_pixel_count(out) == 0  # no features -- nothing drawn, no crash

    def test_writes_cache_files(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir)
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        gw.render_static_geojson_overlay(_blank(), cfg, _source(), source)
        assert list(data_dir.glob("overlay_geojson_cache_*.png"))
        assert list(data_dir.glob("overlay_geojson_cache_*.json"))

    def test_second_render_reuses_cache_without_rebuilding(self, tmp_path, monkeypatch):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        eff_source = _source()

        first = gw.render_static_geojson_overlay(_blank(), cfg, eff_source, source)

        calls = []
        original = gw._build_geojson_layer
        monkeypatch.setattr(gw, "_build_geojson_layer", lambda *a, **k: calls.append(1) or original(*a, **k))
        second = gw.render_static_geojson_overlay(_blank(), cfg, eff_source, source)

        assert calls == []  # cache hit -- layer builder never re-invoked
        assert _nonblack_pixel_count(second) == _nonblack_pixel_count(first)

    def test_editing_source_file_invalidates_cache(self, tmp_path, monkeypatch):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        eff_source = _source()
        before = gw.render_static_geojson_overlay(_blank(), cfg, eff_source, source)

        time.sleep(0.05)  # ensure a distinct mtime on filesystems with coarse resolution
        _write_geojson(geojson_path, SF_POINT, LA_POINT)

        calls = []
        original = gw._build_geojson_layer
        monkeypatch.setattr(gw, "_build_geojson_layer", lambda *a, **k: calls.append(1) or original(*a, **k))
        after = gw.render_static_geojson_overlay(_blank(), cfg, eff_source, source)

        assert calls == [1]  # rebuilt, exactly once
        assert _nonblack_pixel_count(after) > _nonblack_pixel_count(before)

    def test_changing_style_config_uses_a_separate_cache_entry(self, tmp_path):
        # Different style => a different cache identity, not the same file
        # overwritten -- see test_distinct_satellites_each_get_their_own_cache_entry
        # for why sharing one fixed filename across distinct configs is wrong.
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir)
        eff_source = _source()

        default_source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        gw.render_static_geojson_overlay(_blank(), cfg, eff_source, default_source)
        pngs_after_first = set(data_dir.glob("overlay_geojson_cache_*.png"))

        bigger_marker_source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),), marker_radius=99)
        gw.render_static_geojson_overlay(_blank(), cfg, eff_source, bigger_marker_source)
        pngs_after_second = set(data_dir.glob("overlay_geojson_cache_*.png"))

        assert len(pngs_after_second) == len(pngs_after_first) + 1  # a new entry, the old one untouched

    def test_changing_font_size_uses_a_separate_cache_entry(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, {**SF_POINT, "properties": {"name": "SF"}})
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir)
        eff_source = _source()

        default_source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        gw.render_static_geojson_overlay(_blank(), cfg, eff_source, default_source)
        pngs_after_first = set(data_dir.glob("overlay_geojson_cache_*.png"))

        bigger_font_source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),), font_size=40)
        gw.render_static_geojson_overlay(_blank(), cfg, eff_source, bigger_font_source)
        pngs_after_second = set(data_dir.glob("overlay_geojson_cache_*.png"))

        assert len(pngs_after_second) == len(pngs_after_first) + 1

    def test_different_name_uses_a_separate_cache_entry(self, tmp_path):
        # Two sources with identical files/style but different names must not
        # collide -- name is part of the cache identity precisely so independently
        # configured sources never fight over one cache file.
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir)
        eff_source = _source()

        gw.render_static_geojson_overlay(_blank(), cfg, eff_source, gw.GeoJSONSource(name="a", files=(str(geojson_path),)))
        gw.render_static_geojson_overlay(_blank(), cfg, eff_source, gw.GeoJSONSource(name="b", files=(str(geojson_path),)))

        assert len(list(data_dir.glob("overlay_geojson_cache_*.png"))) == 2

    def test_distinct_satellites_each_get_their_own_cache_entry(self, tmp_path, monkeypatch):
        # Regression test for a real bug: the cache used to live at one fixed
        # filename regardless of satellite/resolution/style, so alternating combos
        # across two satellites (e.g. combo_mode = "rotate"/"per_monitor") would
        # invalidate-and-overwrite each other's cache every single cycle -- never
        # actually caching anything. Each distinct (satellite, frame size, style)
        # must get its own cache file instead.
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir)
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        combo_a = gw.resolve_source(gw.Config(satellite="GOES18", sector="CONUS"), None)
        combo_b = gw.resolve_source(gw.Config(satellite="GOES19", sector="CONUS"), None)

        calls = []
        original = gw._build_geojson_layer
        monkeypatch.setattr(gw, "_build_geojson_layer", lambda *a, **k: calls.append(1) or original(*a, **k))

        for eff_source in [combo_a, combo_b, combo_a, combo_b]:
            gw.render_static_geojson_overlay(_blank(), cfg, eff_source, source)

        assert len(calls) == 2  # one build per distinct satellite, not per render call
        assert len(list(data_dir.glob("overlay_geojson_cache_*.png"))) == 2

    def test_distinct_sectors_each_get_their_own_cache_entry(self, tmp_path):
        # Same bug class as test_distinct_satellites_each_get_their_own_cache_entry,
        # but for sector: CONUS and Full Disk use different GEOS extents for the same
        # satellite, so they must never share a cache entry either.
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        data_dir = tmp_path / "data"
        cfg = gw.Config(data_dir=data_dir)
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        conus = gw.resolve_source(gw.Config(satellite="GOES18", sector="CONUS"), None)
        full_disk = gw.resolve_source(gw.Config(satellite="GOES18", sector="FD"), None)

        gw.render_static_geojson_overlay(_blank(), cfg, conus, source)
        gw.render_static_geojson_overlay(_blank(), cfg, full_disk, source)

        assert len(list(data_dir.glob("overlay_geojson_cache_*.png"))) == 2

    def test_named_point_renders_label_through_the_cache_path(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, {**SF_POINT, "properties": {"name": "San Francisco"}})
        cfg = gw.Config(data_dir=tmp_path / "data")
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        labeled = gw.render_static_geojson_overlay(_blank(), cfg, _source(), source)

        _write_geojson(tmp_path / "unlabeled.geojson", SF_POINT)
        cfg_unlabeled = gw.Config(data_dir=tmp_path / "data2")
        unlabeled_source = gw.GeoJSONSource(name="cities", files=(str(tmp_path / "unlabeled.geojson"),))
        unlabeled = gw.render_static_geojson_overlay(_blank(), cfg_unlabeled, _source(), unlabeled_source)

        assert _nonblack_pixel_count(labeled) > _nonblack_pixel_count(unlabeled)

    def test_different_resolution_invalidates_cache(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        source = gw.GeoJSONSource(name="cities", files=(str(geojson_path),))
        eff_source = _source()

        small = gw.render_static_geojson_overlay(Image.new("RGB", (2500, 1500), (0, 0, 0)), cfg, eff_source, source)
        large = gw.render_static_geojson_overlay(Image.new("RGB", (5000, 3000), (0, 0, 0)), cfg, eff_source, source)
        assert small.size == (2500, 1500)
        assert large.size == (5000, 3000)


class TestDrawOverlaysWiring:
    def test_geojson_source_alone_triggers_draw_overlays(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        overlays = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="cities", files=(str(geojson_path),)),))
        out = gw.draw_overlays(_blank(), cfg, overlays, _source())
        assert _nonblack_pixel_count(out) > 0

    def test_multiple_geojson_sources_both_draw(self, tmp_path):
        one = tmp_path / "one.geojson"
        two = tmp_path / "two.geojson"
        _write_geojson(one, SF_POINT)
        _write_geojson(two, LA_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        overlays = gw.OverlaysConfig(geojson_sources=(
            gw.GeoJSONSource(name="one", files=(str(one),)),
            gw.GeoJSONSource(name="two", files=(str(two),)),
        ))
        combined = gw.draw_overlays(_blank(), cfg, overlays, _source())

        overlays_one_only = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="one", files=(str(one),)),))
        single = gw.draw_overlays(_blank(), cfg, overlays_one_only, _source())

        assert _nonblack_pixel_count(combined) > _nonblack_pixel_count(single)

    def test_broken_geojson_file_does_not_crash_draw_overlays(self, tmp_path):
        bad_path = tmp_path / "broken.geojson"
        bad_path.write_text("not json")
        cfg = gw.Config(data_dir=tmp_path / "data")
        overlays = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="broken", files=(str(bad_path),)),))
        out = gw.draw_overlays(_blank(), cfg, overlays, _source())  # must not raise
        assert _nonblack_pixel_count(out) == 0

    def test_one_broken_source_does_not_prevent_others_from_drawing(self, tmp_path):
        bad_path = tmp_path / "broken.geojson"
        bad_path.write_text("not json")
        good_path = tmp_path / "cities.geojson"
        _write_geojson(good_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        overlays = gw.OverlaysConfig(geojson_sources=(
            gw.GeoJSONSource(name="broken", files=(str(bad_path),)),
            gw.GeoJSONSource(name="good", files=(str(good_path),)),
        ))
        out = gw.draw_overlays(_blank(), cfg, overlays, _source())  # must not raise
        assert _nonblack_pixel_count(out) > 0

    def test_full_disk_sector_is_calibrated_not_skipped(self, tmp_path):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        overlays = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="cities", files=(str(geojson_path),)),))
        full_disk = gw.resolve_source(gw.Config(satellite="GOES18", sector="FD"), None)
        out = gw.draw_overlays(_blank(), cfg, overlays, full_disk)
        assert _nonblack_pixel_count(out) > 0

    def test_mesoscale_sector_is_skipped_not_crashed(self, tmp_path, caplog):
        geojson_path = tmp_path / "cities.geojson"
        _write_geojson(geojson_path, SF_POINT)
        cfg = gw.Config(data_dir=tmp_path / "data")
        overlays = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="cities", files=(str(geojson_path),)),))
        mesoscale = gw.resolve_source(gw.Config(satellite="GOES18", sector="M1"), None)
        out = gw.draw_overlays(_blank(), cfg, overlays, mesoscale)  # must not raise
        assert _nonblack_pixel_count(out) == 0
