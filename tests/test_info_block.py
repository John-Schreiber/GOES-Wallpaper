# tests/test_info_block.py -- _fit_info_bar_font overlap avoidance
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from PIL import Image, ImageDraw, ImageFont

import goes_wallpaper as gw


@pytest.fixture
def font_path(tmp_path):
    """A real, scalable TrueType font usable with ImageFont.truetype(path, size) on any
    OS/CI runner, without committing a binary font file to the repo: Pillow already
    bundles one (the Aileron font used by ImageFont.load_default) -- write its bytes
    out to a temp file so _fit_info_bar_font's real shrink logic gets exercised
    instead of silently falling back to the tiny fixed-size default font."""
    path = tmp_path / "test_font.ttf"
    path.write_bytes(ImageFont.load_default(size=40).font_bytes)
    return str(path)


def make_draw():
    return ImageDraw.Draw(Image.new("RGBA", (10, 10)))


class TestFitInfoBarFont:
    def test_short_texts_keep_nominal_size(self, font_path):
        draw = make_draw()
        font = gw._fit_info_bar_font(draw, "GOES-19 (East)  •  CONUS  •  GEOCOLOR", "Captured 2026-07-16 12:00 EDT", font_path, 40, 2000, 20)
        assert font.size == 40

    def test_long_texts_on_narrow_bar_shrink_to_avoid_overlap(self, font_path):
        draw = make_draw()
        left = "GOES-18 (West)  •  Full Disk  •  GeoColor (satpy_raw)"
        right = "Captured 2026-07-16 20:50 Eastern Daylight Time"
        width = 800
        pad = 20
        font = gw._fit_info_bar_font(draw, left, right, font_path, 40, width, pad)
        assert font.size < 40
        total = draw.textlength(left, font=font) + draw.textlength(right, font=font) + 3 * pad
        assert total <= width

    def test_never_shrinks_below_floor(self, font_path):
        draw = make_draw()
        left = "GOES-18 (West)  •  Full Disk  •  GeoColor (satpy_raw) " * 5
        right = "Captured 2026-07-16 20:50 Eastern Daylight Time"
        font = gw._fit_info_bar_font(draw, left, right, font_path, 40, 100, 20)
        assert font.size >= gw._INFO_BAR_MIN_FONT_PX
