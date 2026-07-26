# tests/test_image_math.py -- crop_fractional, crop_to_screen pixel math
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from PIL import Image

import goes_wallpaper as gw


def make_image(w, h):
    return Image.new("RGB", (w, h), (0, 0, 0))


class TestCropFractional:
    def test_no_op_box_returns_same_image_object(self):
        img = make_image(100, 100)
        result = gw.crop_fractional(img, 0.0, 0.0, 1.0, 1.0)
        assert result is img  # early-return identity, not just equal size

    def test_crops_to_expected_pixel_box(self):
        img = make_image(1000, 500)
        result = gw.crop_fractional(img, 0.1, 0.2, 0.9, 0.8)
        assert result.size == (800, 300)  # (0.9-0.1)*1000, (0.8-0.2)*500

    def test_left_half_crop(self):
        img = make_image(200, 100)
        result = gw.crop_fractional(img, 0.0, 0.0, 0.5, 1.0)
        assert result.size == (100, 100)


class TestCropToScreen:
    def test_result_matches_target_size_exactly(self):
        img = make_image(2500, 1500)
        result = gw.crop_to_screen(img, (1920, 1080), 0.5)
        assert result.size == (1920, 1080)

    def test_wider_target_than_source_aspect(self):
        # Source is taller/narrower relative to a very wide target -> scale limited by width
        img = make_image(1000, 1000)
        result = gw.crop_to_screen(img, (2000, 500), 0.5)
        assert result.size == (2000, 500)

    def test_center_anchor_is_symmetric(self):
        # A source with a distinct left/right marker, cropped to a narrower target with
        # anchor=0.5, should keep the exact center column range.
        img = Image.new("RGB", (100, 100))
        for x in range(100):
            for y in range(100):
                img.putpixel((x, y), (x, 0, 0))
        result = gw.crop_to_screen(img, (50, 100), 0.5)
        # Cover-crop at same height (scale=1x), width 100->50, anchor 0.5 keeps columns 25..75
        assert result.getpixel((0, 0))[0] == 25
        assert result.getpixel((49, 0))[0] == 74

    def test_anchor_zero_keeps_top_left(self):
        img = Image.new("RGB", (100, 100))
        for x in range(100):
            img.putpixel((x, 0), (x, 0, 0))
        result = gw.crop_to_screen(img, (50, 100), 0.0)
        assert result.getpixel((0, 0))[0] == 0

    def test_anchor_one_keeps_bottom_right(self):
        img = Image.new("RGB", (100, 100))
        for x in range(100):
            img.putpixel((x, 0), (x, 0, 0))
        result = gw.crop_to_screen(img, (50, 100), 1.0)
        assert result.getpixel((49, 0))[0] == 99


class TestApplyStyleToCanvas:
    """apply_style_to_canvas bakes wallpaper_style onto a target canvas ourselves,
    for pipeline_mode = "files" output -- there's no OS wallpaper renderer to
    delegate this to for a plain file (see run_once_files)."""

    def test_fill_matches_crop_to_screen(self):
        img = make_image(2500, 1500)
        result = gw.apply_style_to_canvas(img, "fill", 1920, 1080)
        assert result.size == (1920, 1080)

    def test_stretch_ignores_aspect_ratio(self):
        img = make_image(1000, 500)
        result = gw.apply_style_to_canvas(img, "stretch", 300, 300)
        assert result.size == (300, 300)

    def test_fit_preserves_aspect_ratio_and_pads_to_target_size(self):
        img = Image.new("RGB", (1000, 500), (200, 100, 50))  # 2:1 aspect
        result = gw.apply_style_to_canvas(img, "fit", 400, 400)
        assert result.size == (400, 400)
        # Scaled to fit width (400x200 at y=100..300), so top/bottom padding, not
        # left/right.
        assert result.getpixel((0, 0)) == (0, 0, 0)  # padding, above the scaled image
        assert result.getpixel((200, 200)) == (200, 100, 50)  # inside the scaled image

    def test_center_pads_a_smaller_image_onto_the_target_canvas(self):
        img = Image.new("RGB", (10, 10), (200, 100, 50))
        result = gw.apply_style_to_canvas(img, "center", 40, 40)
        assert result.size == (40, 40)
        assert result.getpixel((0, 0)) == (0, 0, 0)  # outside the centered image
        assert result.getpixel((20, 20)) == (200, 100, 50)  # inside it

    def test_center_crops_a_larger_image_to_the_target_canvas(self):
        img = make_image(100, 100)
        result = gw.apply_style_to_canvas(img, "center", 40, 40)
        assert result.size == (40, 40)

    def test_tile_repeats_the_image_across_the_canvas(self):
        img = Image.new("RGB", (10, 10), (200, 100, 50))
        result = gw.apply_style_to_canvas(img, "tile", 25, 15)
        assert result.size == (25, 15)
        # A 10x10 tile repeated across a 25x15 canvas covers every pixel (the last
        # tile's paste clips at the canvas edge but still fills up to it).
        assert result.getpixel((0, 0)) == (200, 100, 50)
        assert result.getpixel((20, 10)) == (200, 100, 50)  # third tile column/row
        assert result.getpixel((24, 14)) == (200, 100, 50)  # last (clipped) tile

    def test_span_degrades_to_fill_and_logs_a_warning(self, caplog):
        import logging
        img = make_image(2500, 1500)
        with caplog.at_level(logging.WARNING):
            result = gw.apply_style_to_canvas(img, "span", 1920, 1080)
        assert result.size == (1920, 1080)
        assert any("span" in r.message for r in caplog.records)

    def test_unknown_style_raises(self):
        img = make_image(10, 10)
        with pytest.raises(ValueError, match="Unknown wallpaper_style"):
            gw.apply_style_to_canvas(img, "bogus", 10, 10)
