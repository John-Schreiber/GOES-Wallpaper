# tests/test_migrate_overlay_config.py -- migrate_overlay_config.py
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import tomllib

import migrate_overlay_config as migrate

OLD_CONFIG = '''
satellite = "GOES19"
sector = "CONUS"
overlay_graticule = true
overlay_graticule_step_deg = 15.0
overlay_graticule_color = [1, 2, 3]
overlay_graticule_opacity = 100
overlay_city_marker_radius = 7
overlay_city_color = [4, 5, 6]
overlay_city_font_size = 20
overlay_geojson_files = ["counties.geojson"]
overlay_geojson_color = [7, 8, 9]
overlay_geojson_line_width = 2
overlay_shell_command = ["python", "fetch.py"]
overlay_shell_timeout = 5.0
overlay_shell_color = [10, 11, 12]

[[overlay_cities]]
name = "Portland, ME"
lon = -70.2568
lat = 43.6591

[[overlay_cities]]
name = "Denver"
lon = -104.99
lat = 39.74
'''


def _write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


class TestMigrate:
    def test_no_overlay_keys_is_a_noop(self, tmp_path, capsys):
        p = _write(tmp_path, 'satellite = "GOES19"\n')
        migrate.migrate(p)
        assert not (tmp_path / "overlays.toml").exists()
        assert "nothing to migrate" in capsys.readouterr().out

    def test_backs_up_original(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        original_text = p.read_text()
        migrate.migrate(p)
        assert (tmp_path / "config.toml.bak").read_text() == original_text

    def test_config_toml_has_no_leftover_overlay_keys(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        with p.open("rb") as f:
            values = tomllib.load(f)
        assert not [k for k in values if k.startswith("overlay_")]
        assert values["satellite"] == "GOES19"  # unrelated settings untouched

    def test_config_toml_still_parses_as_valid_toml(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        with p.open("rb") as f:
            tomllib.load(f)  # must not raise

    def test_overlays_toml_graticule(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        with (tmp_path / "overlays.toml").open("rb") as f:
            overlays = tomllib.load(f)
        assert overlays["graticule"] == {
            "enabled": True, "step_deg": 15.0, "color": [1, 2, 3], "opacity": 100,
        }

    def test_overlays_toml_geojson_sources(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        with (tmp_path / "overlays.toml").open("rb") as f:
            overlays = tomllib.load(f)
        names = [s["name"] for s in overlays["geojson_sources"]]
        assert "cities" in names
        assert "default" in names
        default_source = next(s for s in overlays["geojson_sources"] if s["name"] == "default")
        assert default_source["files"] == ["counties.geojson"]
        assert default_source["color"] == [7, 8, 9]
        assert default_source["line_width"] == 2

    def test_overlays_toml_shell_sources(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        with (tmp_path / "overlays.toml").open("rb") as f:
            overlays = tomllib.load(f)
        assert len(overlays["shell_sources"]) == 1
        source = overlays["shell_sources"][0]
        assert source["command"] == ["python", "fetch.py"]
        assert source["timeout"] == 5.0
        assert source["color"] == [10, 11, 12]

    def test_cities_geojson_written(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        geojson = json.loads((tmp_path / "overlays" / "cities.geojson").read_text())
        names = [f["properties"]["name"] for f in geojson["features"]]
        assert names == ["Portland, ME", "Denver"]
        coords = geojson["features"][0]["geometry"]["coordinates"]
        assert coords == [-70.2568, 43.6591]

    def test_cities_source_references_geojson_file(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)
        with (tmp_path / "overlays.toml").open("rb") as f:
            overlays = tomllib.load(f)
        cities_source = next(s for s in overlays["geojson_sources"] if s["name"] == "cities")
        assert cities_source["files"] == ["overlays/cities.geojson"]
        assert cities_source["color"] == [4, 5, 6]
        assert cities_source["marker_radius"] == 7
        assert cities_source["font_size"] == 20

    def test_only_overlay_cities_no_other_overlay_keys(self, tmp_path):
        p = _write(tmp_path, '[[overlay_cities]]\nname = "SF"\nlon = -122.42\nlat = 37.77\n')
        migrate.migrate(p)
        with (tmp_path / "overlays.toml").open("rb") as f:
            overlays = tomllib.load(f)
        assert len(overlays["geojson_sources"]) == 1
        assert "shell_sources" not in overlays

    def test_refuses_to_overwrite_existing_overlays_toml(self, tmp_path):
        p = _write(tmp_path, OLD_CONFIG)
        (tmp_path / "overlays.toml").write_text("# already here\n")
        try:
            migrate.migrate(p)
            raised = False
        except SystemExit:
            raised = True
        assert raised
        assert (tmp_path / "overlays.toml").read_text() == "# already here\n"

    def test_migrated_files_load_cleanly_through_goes_wallpaper(self, tmp_path):
        import goes_wallpaper as gw

        p = _write(tmp_path, OLD_CONFIG)
        migrate.migrate(p)

        cfg = gw.load_config(p, {})
        overlays = gw.load_overlays_config(tmp_path / "overlays.toml")
        gw.validate_overlays_config(overlays)
        assert cfg.satellite == "GOES19"
        assert len(overlays.geojson_sources) == 2
