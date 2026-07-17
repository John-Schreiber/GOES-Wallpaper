# tests/test_info_block.py -- _fit_info_bar_font overlap avoidance
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

from PIL import Image, ImageDraw

import goes_wallpaper as gw

FONT_PATH = r"C:\Windows\Fonts\segoeui.ttf"


def make_draw():
    return ImageDraw.Draw(Image.new("RGBA", (10, 10)))


class TestFitInfoBarFont:
    def test_short_texts_keep_nominal_size(self):
        draw = make_draw()
        font = gw._fit_info_bar_font(draw, "GOES-19 (East)  •  CONUS  •  GEOCOLOR", "Captured 2026-07-16 12:00 EDT", FONT_PATH, 40, 2000, 20)
        assert font.size == 40

    def test_long_texts_on_narrow_bar_shrink_to_avoid_overlap(self):
        draw = make_draw()
        left = "GOES-18 (West)  •  Full Disk  •  GeoColor (satpy_raw)"
        right = "Captured 2026-07-16 20:50 Eastern Daylight Time"
        width = 800
        pad = 20
        font = gw._fit_info_bar_font(draw, left, right, FONT_PATH, 40, width, pad)
        assert font.size < 40
        total = draw.textlength(left, font=font) + draw.textlength(right, font=font) + 3 * pad
        assert total <= width

    def test_never_shrinks_below_floor(self):
        draw = make_draw()
        left = "GOES-18 (West)  •  Full Disk  •  GeoColor (satpy_raw) " * 5
        right = "Captured 2026-07-16 20:50 Eastern Daylight Time"
        font = gw._fit_info_bar_font(draw, left, right, FONT_PATH, 40, 100, 20)
        assert font.size >= gw._INFO_BAR_MIN_FONT_PX
