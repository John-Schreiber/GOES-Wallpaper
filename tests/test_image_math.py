# tests/test_image_math.py -- crop_fractional, crop_to_screen pixel math
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

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
