# tests/test_source_resolution.py -- resolve_source, build_image_url, EffectiveSource
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import goes_wallpaper as gw


def test_build_image_url_fixed_resolution():
    url = gw.build_image_url("GOES18", "CONUS", "GEOCOLOR", "2500x1500")
    assert url == "https://cdn.star.nesdis.noaa.gov/GOES18/ABI/CONUS/GEOCOLOR/2500x1500.jpg"


def test_build_image_url_latest():
    url = gw.build_image_url("GOES19", "FD", "13", "latest")
    assert url == "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/13/latest.jpg"


def test_resolve_source_default_uses_top_level_config():
    cfg = gw.Config(satellite="GOES18", sector="FD", product="13", resolution="1808x1808")
    source = gw.resolve_source(cfg, None)
    assert source.name == "default"
    assert source.satellite == "GOES18"
    assert source.sector == "FD"
    assert source.product == "13"
    assert source.resolution == "1808x1808"
    assert source.crop_left == cfg.source_crop_left


def test_resolve_source_combo_overrides_only_set_fields():
    cfg = gw.Config(satellite="GOES19", sector="CONUS", product="GEOCOLOR", resolution="5000x3000")
    combo = gw.Combo(name="west_ir", satellite="GOES18", product="13")
    source = gw.resolve_source(cfg, combo)
    assert source.name == "west_ir"
    assert source.satellite == "GOES18"  # overridden
    assert source.product == "13"  # overridden
    assert source.sector == "CONUS"  # fell back to cfg
    assert source.resolution == "5000x3000"  # fell back to cfg


def test_resolve_source_combo_crop_always_applies_even_when_default():
    cfg = gw.Config(source_crop_left=0.2)
    combo = gw.Combo(name="c", crop_left=0.0)  # combo's own default (0.0), not cfg's 0.2
    source = gw.resolve_source(cfg, combo)
    assert source.crop_left == 0.0


def test_effective_source_key_distinguishes_sources():
    cfg = gw.Config()
    a = gw.resolve_source(cfg, gw.Combo(name="a", product="GEOCOLOR"))
    b = gw.resolve_source(cfg, gw.Combo(name="b", product="13"))
    assert a.key != b.key
    assert a.key == f"{a.satellite}/{a.sector}/{a.product}/{a.resolution}"


def test_effective_source_image_url_matches_build_image_url():
    cfg = gw.Config(satellite="GOES18", sector="CONUS", product="GEOCOLOR", resolution="5000x3000")
    source = gw.resolve_source(cfg, None)
    assert source.image_url == gw.build_image_url("GOES18", "CONUS", "GEOCOLOR", "5000x3000")


def test_satellite_and_sector_labels_fall_back_to_raw_code():
    source = gw.resolve_source(gw.Config(satellite="GOES18", sector="CONUS"), None)
    assert source.satellite_label() == "GOES-18 (West)"
    assert source.sector_label() == "Continental US"

    unknown = gw.resolve_source(gw.Config(satellite="GOES99", sector="ZZ"), None)
    assert unknown.satellite_label() == "GOES99"
    assert unknown.sector_label() == "ZZ"
