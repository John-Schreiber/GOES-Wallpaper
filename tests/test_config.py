# tests/test_config.py -- config loading, combo parsing, validate_combos
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

import goes_wallpaper as gw


def write_toml(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


class _FakePlatform:
    """Duck-typed stub -- load_config only calls default_data_dir/default_font_path,
    so no need to implement every WallpaperPlatform abstract method."""

    def default_data_dir(self):
        return Path("/platform-default-data-dir")

    def default_font_path(self):
        return "/platform/default/font.ttf"


class TestLoadConfig:
    def test_missing_file_uses_defaults(self, tmp_path):
        cfg = gw.load_config(tmp_path / "does-not-exist.toml", {})
        assert cfg.satellite == "GOES19"
        assert cfg.combo_mode == "single"

    def test_toml_values_override_defaults(self, tmp_path):
        p = write_toml(tmp_path, 'satellite = "GOES18"\nsector = "FD"\n')
        cfg = gw.load_config(p, {})
        assert cfg.satellite == "GOES18"
        assert cfg.sector == "FD"

    def test_cli_overrides_win_over_toml(self, tmp_path):
        p = write_toml(tmp_path, 'satellite = "GOES18"\n')
        cfg = gw.load_config(p, {"satellite": "GOES19"})
        assert cfg.satellite == "GOES19"

    def test_none_overrides_are_ignored(self, tmp_path):
        p = write_toml(tmp_path, 'satellite = "GOES18"\n')
        cfg = gw.load_config(p, {"satellite": None})
        assert cfg.satellite == "GOES18"

    def test_unknown_top_level_key_raises(self, tmp_path):
        p = write_toml(tmp_path, 'bogus_field = 1\n')
        with pytest.raises(ValueError, match="Unknown config key"):
            gw.load_config(p, {})

    def test_data_dir_becomes_path(self, tmp_path):
        p = write_toml(tmp_path, 'data_dir = "C:/somewhere"\n')
        cfg = gw.load_config(p, {})
        assert isinstance(cfg.data_dir, gw.Path)

    def test_no_platform_keeps_config_class_defaults(self, tmp_path):
        p = write_toml(tmp_path, "")
        cfg = gw.load_config(p, {})
        assert cfg.data_dir == gw.DEFAULT_DATA_DIR
        assert cfg.info_font_path == r"C:\Windows\Fonts\segoeui.ttf"

    def test_platform_supplies_defaults_when_unset(self, tmp_path):
        p = write_toml(tmp_path, "")
        cfg = gw.load_config(p, {}, platform=_FakePlatform())
        assert cfg.data_dir == Path("/platform-default-data-dir")
        assert cfg.info_font_path == "/platform/default/font.ttf"

    def test_explicit_data_dir_wins_over_platform_default(self, tmp_path):
        p = write_toml(tmp_path, 'data_dir = "C:/explicit"\n')
        cfg = gw.load_config(p, {}, platform=_FakePlatform())
        assert cfg.data_dir == Path("C:/explicit")

    def test_cli_override_wins_over_platform_default(self, tmp_path):
        p = write_toml(tmp_path, "")
        cfg = gw.load_config(p, {"info_font_path": "/cli/font.ttf"}, platform=_FakePlatform())
        assert cfg.info_font_path == "/cli/font.ttf"

    def test_retry_statuses_becomes_tuple(self, tmp_path):
        p = write_toml(tmp_path, "retry_statuses = [500, 502]\n")
        cfg = gw.load_config(p, {})
        assert cfg.retry_statuses == (500, 502)

    def test_color_fields_become_tuples(self, tmp_path):
        p = write_toml(tmp_path, "overlay_graticule_color = [1, 2, 3]\noverlay_city_color = [4, 5, 6]\n")
        cfg = gw.load_config(p, {})
        assert cfg.overlay_graticule_color == (1, 2, 3)
        assert cfg.overlay_city_color == (4, 5, 6)

    def test_overlay_shell_command_becomes_tuple(self, tmp_path):
        p = write_toml(tmp_path, 'overlay_shell_command = ["python", "fetch.py"]\n')
        cfg = gw.load_config(p, {})
        assert cfg.overlay_shell_command == ("python", "fetch.py")

    def test_overlay_shell_color_becomes_tuple(self, tmp_path):
        p = write_toml(tmp_path, "overlay_shell_color = [7, 8, 9]\n")
        cfg = gw.load_config(p, {})
        assert cfg.overlay_shell_color == (7, 8, 9)

    def test_overlay_geojson_files_becomes_tuple(self, tmp_path):
        p = write_toml(tmp_path, 'overlay_geojson_files = ["a.geojson", "b.geojson"]\n')
        cfg = gw.load_config(p, {})
        assert cfg.overlay_geojson_files == ("a.geojson", "b.geojson")

    def test_overlay_geojson_color_becomes_tuple(self, tmp_path):
        p = write_toml(tmp_path, "overlay_geojson_color = [10, 11, 12]\n")
        cfg = gw.load_config(p, {})
        assert cfg.overlay_geojson_color == (10, 11, 12)

    def test_combos_parsed_into_dataclasses(self, tmp_path):
        p = write_toml(tmp_path, '''
combo_mode = "rotate"

[[combos]]
name = "a"
product = "GEOCOLOR"

[[combos]]
name = "b"
product = "13"
monitor = 1
''')
        cfg = gw.load_config(p, {})
        assert cfg.combo_mode == "rotate"
        assert len(cfg.combos) == 2
        assert all(isinstance(c, gw.Combo) for c in cfg.combos)
        assert cfg.combos[0].name == "a"
        assert cfg.combos[1].monitor == 1

    def test_unknown_combo_key_raises(self, tmp_path):
        p = write_toml(tmp_path, '''
[[combos]]
name = "a"
bogus_key = 1
''')
        with pytest.raises(ValueError, match=r"combos\[0\]"):
            gw.load_config(p, {})

    def test_overlay_cities_parsed_into_dataclasses(self, tmp_path):
        p = write_toml(tmp_path, '''
[[overlay_cities]]
name = "SF"
lon = -122.42
lat = 37.77
''')
        cfg = gw.load_config(p, {})
        assert len(cfg.overlay_cities) == 1
        assert isinstance(cfg.overlay_cities[0], gw.CityMarker)
        assert cfg.overlay_cities[0].name == "SF"

    def test_unknown_overlay_city_key_raises(self, tmp_path):
        p = write_toml(tmp_path, '''
[[overlay_cities]]
name = "SF"
bogus_key = 1
''')
        with pytest.raises(ValueError, match=r"overlay_cities\[0\]"):
            gw.load_config(p, {})


class TestValidateCombos:
    def test_single_mode_always_valid(self):
        gw.validate_combos(gw.Config(combo_mode="single"))  # no raise, even with no combos

    def test_bogus_mode_raises(self):
        with pytest.raises(ValueError, match="combo_mode must be one of"):
            gw.validate_combos(gw.Config(combo_mode="bogus"))

    @pytest.mark.parametrize("mode", ["rotate", "per_monitor"])
    def test_empty_combos_raises(self, mode):
        with pytest.raises(ValueError, match="requires at least one"):
            gw.validate_combos(gw.Config(combo_mode=mode, combos=()))

    def test_duplicate_combo_names_raise(self):
        cfg = gw.Config(combo_mode="rotate", combos=(gw.Combo(name="a"), gw.Combo(name="a")))
        with pytest.raises(ValueError, match="unique"):
            gw.validate_combos(cfg)

    def test_per_monitor_requires_monitor_on_every_combo(self):
        cfg = gw.Config(
            combo_mode="per_monitor",
            combos=(gw.Combo(name="a", monitor=0), gw.Combo(name="b")),
        )
        with pytest.raises(ValueError, match="requires every combo to set `monitor`"):
            gw.validate_combos(cfg)

    def test_per_monitor_requires_unique_monitor_indices(self):
        cfg = gw.Config(
            combo_mode="per_monitor",
            combos=(gw.Combo(name="a", monitor=0), gw.Combo(name="b", monitor=0)),
        )
        with pytest.raises(ValueError, match="unique"):
            gw.validate_combos(cfg)

    def test_valid_per_monitor_config_passes(self):
        cfg = gw.Config(
            combo_mode="per_monitor",
            combos=(gw.Combo(name="a", monitor=0), gw.Combo(name="b", monitor=1)),
        )
        gw.validate_combos(cfg)  # no raise

    def test_rotate_does_not_require_monitor(self):
        cfg = gw.Config(combo_mode="rotate", combos=(gw.Combo(name="a"), gw.Combo(name="b")))
        gw.validate_combos(cfg)  # no raise


class TestValidateSourceKind:
    def test_default_is_valid(self):
        gw.validate_source_kind(gw.Config())  # no raise

    def test_satpy_raw_is_valid(self):
        gw.validate_source_kind(gw.Config(source_kind="satpy_raw"))  # no raise

    def test_bogus_top_level_source_kind_raises(self):
        with pytest.raises(ValueError, match="source_kind must be one of"):
            gw.validate_source_kind(gw.Config(source_kind="bogus"))

    def test_combo_source_kind_none_is_ignored(self):
        cfg = gw.Config(combos=(gw.Combo(name="a"),))
        gw.validate_source_kind(cfg)  # no raise

    def test_combo_source_kind_valid_passes(self):
        cfg = gw.Config(combos=(gw.Combo(name="a", source_kind="satpy_raw"),))
        gw.validate_source_kind(cfg)  # no raise

    def test_bogus_combo_source_kind_raises(self):
        cfg = gw.Config(combos=(gw.Combo(name="a", source_kind="bogus"),))
        with pytest.raises(ValueError, match=r"combos\['a'\].source_kind must be one of"):
            gw.validate_source_kind(cfg)


class TestValidateLonlatCropBounds:
    def test_default_is_valid(self):
        gw.validate_lonlat_crop_bounds(gw.Config())  # no raise

    def test_all_four_set_and_valid_passes(self):
        cfg = gw.Config(source_crop_min_lon=-110.0, source_crop_min_lat=30.0, source_crop_max_lon=-90.0, source_crop_max_lat=45.0)
        gw.validate_lonlat_crop_bounds(cfg)  # no raise

    @pytest.mark.parametrize("field", ["source_crop_min_lon", "source_crop_min_lat", "source_crop_max_lon", "source_crop_max_lat"])
    def test_partial_set_raises(self, field):
        cfg = gw.Config(**{field: 1.0})
        with pytest.raises(ValueError, match="must all be set together"):
            gw.validate_lonlat_crop_bounds(cfg)

    def test_min_lon_not_less_than_max_lon_raises(self):
        cfg = gw.Config(source_crop_min_lon=-90.0, source_crop_min_lat=30.0, source_crop_max_lon=-110.0, source_crop_max_lat=45.0)
        with pytest.raises(ValueError, match="min_lon"):
            gw.validate_lonlat_crop_bounds(cfg)

    def test_min_lat_not_less_than_max_lat_raises(self):
        cfg = gw.Config(source_crop_min_lon=-110.0, source_crop_min_lat=45.0, source_crop_max_lon=-90.0, source_crop_max_lat=30.0)
        with pytest.raises(ValueError, match="min_lat"):
            gw.validate_lonlat_crop_bounds(cfg)

    def test_combo_partial_set_raises(self):
        cfg = gw.Config(combos=(gw.Combo(name="a", crop_min_lon=-100.0),))
        with pytest.raises(ValueError, match=r"combos\['a'\].*must all be set together"):
            gw.validate_lonlat_crop_bounds(cfg)

    def test_combo_fully_set_and_valid_passes(self):
        cfg = gw.Config(combos=(gw.Combo(name="a", crop_min_lon=-100.0, crop_min_lat=30.0, crop_max_lon=-90.0, crop_max_lat=40.0),))
        gw.validate_lonlat_crop_bounds(cfg)  # no raise


class TestValidateOutputProjection:
    def test_default_is_valid(self):
        gw.validate_output_projection(gw.Config())  # no raise

    def test_orthographic_needs_no_bounds(self):
        gw.validate_output_projection(gw.Config(output_projection="orthographic"))  # no raise

    def test_bogus_projection_raises(self):
        with pytest.raises(ValueError, match="output_projection must be one of"):
            gw.validate_output_projection(gw.Config(output_projection="bogus"))

    def test_platecarree_without_bounds_raises(self):
        with pytest.raises(ValueError, match="requires a complete lon/lat crop box"):
            gw.validate_output_projection(gw.Config(output_projection="platecarree"))

    def test_platecarree_with_top_level_bounds_passes(self):
        cfg = gw.Config(
            output_projection="platecarree",
            source_crop_min_lon=-110.0, source_crop_min_lat=30.0, source_crop_max_lon=-90.0, source_crop_max_lat=45.0,
        )
        gw.validate_output_projection(cfg)  # no raise

    def test_platecarree_combo_without_bounds_and_no_top_level_fallback_raises(self):
        cfg = gw.Config(output_projection="platecarree", combos=(gw.Combo(name="a"),))
        with pytest.raises(ValueError, match=r"combos\['a'\]"):
            gw.validate_output_projection(cfg)

    def test_platecarree_combo_falls_back_to_top_level_bounds(self):
        cfg = gw.Config(
            output_projection="platecarree",
            source_crop_min_lon=-110.0, source_crop_min_lat=30.0, source_crop_max_lon=-90.0, source_crop_max_lat=45.0,
            combos=(gw.Combo(name="a"),),
        )
        gw.validate_output_projection(cfg)  # no raise

    def test_lambertazimuthal_needs_no_bounds(self):
        gw.validate_output_projection(gw.Config(output_projection="lambertazimuthal"))  # no raise

    def test_lambertconformal_without_bounds_raises(self):
        with pytest.raises(ValueError, match="requires a complete lon/lat crop box"):
            gw.validate_output_projection(gw.Config(output_projection="lambertconformal"))

    def test_lambertconformal_with_top_level_bounds_passes(self):
        cfg = gw.Config(
            output_projection="lambertconformal",
            source_crop_min_lon=-125.0, source_crop_min_lat=25.0, source_crop_max_lon=-95.0, source_crop_max_lat=50.0,
        )
        gw.validate_output_projection(cfg)  # no raise

    def test_lambertconformal_default_standard_parallels_pass(self):
        cfg = gw.Config(
            output_projection="lambertconformal",
            source_crop_min_lon=-125.0, source_crop_min_lat=25.0, source_crop_max_lon=-95.0, source_crop_max_lat=50.0,
        )
        gw.validate_output_projection(cfg)  # no raise -- lcc_lat1/lcc_lat2 both unset is fine

    def test_lambertconformal_explicit_standard_parallels_pass(self):
        cfg = gw.Config(
            output_projection="lambertconformal",
            source_crop_min_lon=-125.0, source_crop_min_lat=25.0, source_crop_max_lon=-95.0, source_crop_max_lat=50.0,
            output_projection_lcc_lat1=30.0, output_projection_lcc_lat2=45.0,
        )
        gw.validate_output_projection(cfg)  # no raise

    def test_lambertconformal_partial_standard_parallels_raises(self):
        cfg = gw.Config(
            output_projection="lambertconformal",
            source_crop_min_lon=-125.0, source_crop_min_lat=25.0, source_crop_max_lon=-95.0, source_crop_max_lat=50.0,
            output_projection_lcc_lat1=30.0,
        )
        with pytest.raises(ValueError, match="must both be set together"):
            gw.validate_output_projection(cfg)

    def test_lambertconformal_lat1_not_less_than_lat2_raises(self):
        cfg = gw.Config(
            output_projection="lambertconformal",
            source_crop_min_lon=-125.0, source_crop_min_lat=25.0, source_crop_max_lon=-95.0, source_crop_max_lat=50.0,
            output_projection_lcc_lat1=45.0, output_projection_lcc_lat2=30.0,
        )
        with pytest.raises(ValueError, match="must be less than"):
            gw.validate_output_projection(cfg)

    def test_platecarree_combo_own_bounds_pass_without_top_level(self):
        cfg = gw.Config(
            output_projection="platecarree",
            combos=(gw.Combo(name="a", crop_min_lon=-100.0, crop_min_lat=30.0, crop_max_lon=-90.0, crop_max_lat=40.0),),
        )
        gw.validate_output_projection(cfg)  # no raise


class TestValidatePlatform:
    def test_default_is_valid(self):
        gw.validate_platform(gw.Config())  # no raise

    def test_windows_is_valid(self):
        gw.validate_platform(gw.Config(platform="windows"))  # no raise

    def test_kde_is_valid(self):
        gw.validate_platform(gw.Config(platform="kde"))  # no raise

    def test_bogus_platform_raises(self):
        with pytest.raises(ValueError, match="platform must be one of"):
            gw.validate_platform(gw.Config(platform="bogus"))
