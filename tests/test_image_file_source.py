# tests/test_image_file_source.py -- source_kind = "image_file": load_image_file_bytes,
# _fetch_image_file, and the fetch_frame/render_frame split via the _SOURCES registry
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import replace

import pytest
from PIL import Image

import goes_wallpaper as gw


def _write_png(path, size=(4, 3), color=(10, 20, 30)):
    Image.new("RGB", size, color).save(path, "PNG")


class _FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.last_request_headers = None

    def get(self, url, headers=None, timeout=None):
        self.last_request_headers = headers
        return self._response


class TestLoadImageFileBytesLocalPath:
    def test_reads_bytes_and_uses_mtime_as_etag(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path)
        cfg = gw.Config()
        content, headers = gw.load_image_file_bytes(cfg, _FakeSession(None), str(path), None)
        assert content == path.read_bytes()
        assert headers["etag"] == str(path.stat().st_mtime_ns)

    def test_unchanged_mtime_returns_none(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path)
        cfg = gw.Config()
        mtime_etag = str(path.stat().st_mtime_ns)
        assert gw.load_image_file_bytes(cfg, _FakeSession(None), str(path), mtime_etag) is None

    def test_changed_mtime_returns_content(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path)
        cfg = gw.Config()
        assert gw.load_image_file_bytes(cfg, _FakeSession(None), str(path), "not-the-real-etag") is not None


class TestLoadImageFileBytesUrl:
    def test_fetches_via_session_get(self):
        cfg = gw.Config()
        resp = _FakeResponse(200, content=b"fake-bytes", headers={"ETag": "abc"})
        session = _FakeSession(resp)
        content, headers = gw.load_image_file_bytes(cfg, session, "https://example.com/frame.png", None)
        assert content == b"fake-bytes"
        assert headers["etag"] == "abc"

    def test_sends_if_none_match_when_etag_known(self):
        cfg = gw.Config()
        session = _FakeSession(_FakeResponse(200, content=b"x"))
        gw.load_image_file_bytes(cfg, session, "https://example.com/frame.png", "prev-etag")
        assert session.last_request_headers == {"If-None-Match": "prev-etag"}

    def test_304_returns_none(self):
        cfg = gw.Config()
        session = _FakeSession(_FakeResponse(304))
        assert gw.load_image_file_bytes(cfg, session, "https://example.com/frame.png", "prev-etag") is None


class TestFetchImageFile:
    def test_decodes_png_from_local_path(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path, size=(8, 6))
        cfg = gw.Config(source_kind="image_file", image_path=str(path))
        source = gw.resolve_source(cfg, None)
        frame = gw._fetch_image_file(cfg, _FakeSession(None), source, {})
        assert frame is not None
        assert frame.source_kind == "image_file"
        assert frame.image.size == (8, 6)
        assert frame.area_info is None

    def test_unchanged_mtime_returns_none(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path)
        cfg = gw.Config(source_kind="image_file", image_path=str(path))
        source = gw.resolve_source(cfg, None)
        sstate = {"etag": str(path.stat().st_mtime_ns)}
        assert gw._fetch_image_file(cfg, _FakeSession(None), source, sstate) is None

    def test_unreadable_bytes_raise_value_error(self, tmp_path):
        path = tmp_path / "not_an_image.png"
        path.write_bytes(b"this is not a real image file")
        cfg = gw.Config(source_kind="image_file", image_path=str(path))
        source = gw.resolve_source(cfg, None)
        with pytest.raises(ValueError, match="Could not decode"):
            gw._fetch_image_file(cfg, _FakeSession(None), source, {})

    def test_missing_image_path_raises_value_error(self):
        cfg = gw.Config(source_kind="image_file")
        source = gw.resolve_source(cfg, None)
        with pytest.raises(ValueError, match="requires image_path"):
            gw._fetch_image_file(cfg, _FakeSession(None), source, {})


class TestSourcesRegistry:
    def test_cdn_jpg_strips_baked_caption_and_tracks_etag(self):
        entry = gw._SOURCES["cdn_jpg"]
        assert entry.strips_baked_caption is True
        assert entry.tracks_etag is True

    def test_satpy_raw_does_not_strip_caption_or_track_etag(self):
        entry = gw._SOURCES["satpy_raw"]
        assert entry.strips_baked_caption is False
        assert entry.tracks_etag is False

    def test_image_file_tracks_etag_but_does_not_strip_caption(self):
        entry = gw._SOURCES["image_file"]
        assert entry.strips_baked_caption is False
        assert entry.tracks_etag is True

    def test_fetch_frame_unknown_source_kind_raises(self):
        cfg = gw.Config()
        source = gw.resolve_source(cfg, None)
        bogus_source = replace(source, source_kind="bogus")
        with pytest.raises(ValueError, match="Unknown source_kind"):
            gw.fetch_frame(cfg, _FakeSession(None), bogus_source, {})


class TestHasGeoreferencing:
    """image_file frames have no AreaInfo, same as cdn_jpg -- but must NOT silently
    inherit cdn_jpg's hand-calibrated CONUS/Full Disk lookup (_GEOS_AREA_BY_SECTOR)
    just because Config.satellite/sector default to the calibrated GOES19/CONUS."""

    def test_cdn_jpg_always_has_georeferencing_even_without_area(self):
        source = gw.resolve_source(gw.Config(source_kind="cdn_jpg"), None)
        assert gw._has_georeferencing(source, None) is True

    def test_satpy_raw_has_georeferencing_via_area(self):
        source = gw.resolve_source(gw.Config(source_kind="satpy_raw"), None)
        area = gw.AreaInfo(proj4_params={"proj": "geos"}, extent=(-1.0, -1.0, 1.0, 1.0))
        assert gw._has_georeferencing(source, area) is True

    def test_image_file_has_no_georeferencing_even_with_default_conus_sector(self):
        cfg = gw.Config(source_kind="image_file", image_path="frame.png", satellite="GOES19", sector="CONUS")
        source = gw.resolve_source(cfg, None)
        assert gw._has_georeferencing(source, None) is False


class TestRenderFrame:
    def _minimal_cfg(self):
        return gw.Config(
            crop_to_screen=False,
            trim_source_caption=False,
            info_block=False,
            output_projection="native",
        )

    def test_renders_image_file_frame_and_builds_metadata(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path, size=(8, 6))
        cfg = replace(self._minimal_cfg(), source_kind="image_file", image_path=str(path))
        source = gw.resolve_source(cfg, None)
        frame = gw._fetch_image_file(cfg, _FakeSession(None), source, {})
        assert frame is not None

        state = {}
        img, meta = gw.render_frame(cfg, gw.OverlaysConfig(), source, frame, state, (0, 0), platform=None)

        assert img.size == (8, 6)
        assert meta["product"] == "image_file"
        assert meta["source_url"] == str(path)
        assert meta["http_etag"] == frame.extra_meta["etag"]
        assert state["sources"][source.key]["etag"] == frame.extra_meta["etag"]

    def test_fetch_and_render_composes_fetch_frame_and_render_frame(self, tmp_path):
        path = tmp_path / "frame.png"
        _write_png(path, size=(5, 5))
        cfg = replace(self._minimal_cfg(), source_kind="image_file", image_path=str(path))
        source = gw.resolve_source(cfg, None)
        state = {}
        result = gw.fetch_and_render(cfg, gw.OverlaysConfig(), _FakeSession(None), source, state, (0, 0), platform=None)
        assert result is not None
        img, meta = result
        assert img.size == (5, 5)
        assert meta["source_url"] == str(path)

        # Second call with unchanged mtime: fetch_frame returns None, fetch_and_render
        # propagates that without touching render_frame at all.
        result2 = gw.fetch_and_render(cfg, gw.OverlaysConfig(), _FakeSession(None), source, state, (0, 0), platform=None)
        assert result2 is None

    def test_lonlat_crop_does_not_silently_use_cdn_jpg_calibration(self, tmp_path, caplog):
        """source.satellite/sector default to GOES19/CONUS -- which IS calibrated in
        _GEOS_AREA_BY_SECTOR for cdn_jpg -- but an image_file frame has no area_info
        and isn't cdn_jpg, so it must fall back to the plain fractional crop (a no-op
        here, since crop_left/top/right/bottom default to the full frame) instead of
        silently treating an arbitrary image as if it were a real GOES-19 CONUS
        geostationary frame."""
        path = tmp_path / "frame.png"
        _write_png(path, size=(8, 6))
        cfg = replace(
            self._minimal_cfg(), source_kind="image_file", image_path=str(path),
            source_crop_min_lon=-100.0, source_crop_min_lat=30.0,
            source_crop_max_lon=-80.0, source_crop_max_lat=40.0,
        )
        source = gw.resolve_source(cfg, None)
        frame = gw._fetch_image_file(cfg, _FakeSession(None), source, {})

        with caplog.at_level("WARNING"):
            img, _ = gw.render_frame(cfg, gw.OverlaysConfig(), source, frame, {}, (0, 0), platform=None)

        assert img.size == (8, 6)  # unchanged -- the lon/lat crop was never applied
        assert any("falling back to the fractional crop" in r.message for r in caplog.records)

    def test_output_projection_skipped_without_georeferencing(self, tmp_path, caplog):
        path = tmp_path / "frame.png"
        _write_png(path, size=(8, 6))
        cfg = replace(
            self._minimal_cfg(), source_kind="image_file", image_path=str(path),
            output_projection="platecarree",
            source_crop_min_lon=-100.0, source_crop_min_lat=30.0,
            source_crop_max_lon=-80.0, source_crop_max_lat=40.0,
        )
        source = gw.resolve_source(cfg, None)
        frame = gw._fetch_image_file(cfg, _FakeSession(None), source, {})

        with caplog.at_level("WARNING"):
            img, _ = gw.render_frame(cfg, gw.OverlaysConfig(), source, frame, {}, (0, 0), platform=None)

        assert img.size == (8, 6)  # native projection kept -- reprojection was skipped
        assert any("output_projection skipped" in r.message for r in caplog.records)
