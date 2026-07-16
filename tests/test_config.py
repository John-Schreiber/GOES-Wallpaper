# tests/test_config.py -- config loading, combo parsing, validate_combos
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

import goes_wallpaper as gw


def write_toml(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


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

    def test_retry_statuses_becomes_tuple(self, tmp_path):
        p = write_toml(tmp_path, "retry_statuses = [500, 502]\n")
        cfg = gw.load_config(p, {})
        assert cfg.retry_statuses == (500, 502)

    def test_color_fields_become_tuples(self, tmp_path):
        p = write_toml(tmp_path, "overlay_graticule_color = [1, 2, 3]\noverlay_city_color = [4, 5, 6]\n")
        cfg = gw.load_config(p, {})
        assert cfg.overlay_graticule_color == (1, 2, 3)
        assert cfg.overlay_city_color == (4, 5, 6)

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
