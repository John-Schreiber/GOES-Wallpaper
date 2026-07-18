# tests/test_overlays_config.py -- load_overlays_config, validate_overlays_config
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

import goes_wallpaper as gw


def write_toml(tmp_path, text):
    p = tmp_path / "overlays.toml"
    p.write_text(text)
    return p


class TestLoadOverlaysConfig:
    def test_missing_file_uses_defaults(self, tmp_path):
        overlays = gw.load_overlays_config(tmp_path / "does-not-exist.toml")
        assert overlays.graticule.enabled is False
        assert overlays.geojson_sources == ()
        assert overlays.shell_sources == ()

    def test_empty_file_uses_defaults(self, tmp_path):
        p = write_toml(tmp_path, "")
        overlays = gw.load_overlays_config(p)
        assert overlays.graticule.enabled is False
        assert overlays.geojson_sources == ()
        assert overlays.shell_sources == ()

    def test_unknown_top_level_key_raises(self, tmp_path):
        p = write_toml(tmp_path, "bogus_field = 1\n")
        with pytest.raises(ValueError, match="Unknown overlays config key"):
            gw.load_overlays_config(p)

    def test_graticule_parsed(self, tmp_path):
        p = write_toml(tmp_path, '''
[graticule]
enabled = true
step_deg = 5.0
color = [1, 2, 3]
opacity = 200
''')
        overlays = gw.load_overlays_config(p)
        assert overlays.graticule == gw.GraticuleConfig(enabled=True, step_deg=5.0, color=(1, 2, 3), opacity=200)

    def test_unknown_graticule_key_raises(self, tmp_path):
        p = write_toml(tmp_path, '''
[graticule]
enabled = true
bogus_key = 1
''')
        with pytest.raises(ValueError, match=r"\[graticule\]"):
            gw.load_overlays_config(p)

    def test_geojson_sources_parsed_into_dataclasses(self, tmp_path):
        p = write_toml(tmp_path, '''
[[geojson_sources]]
name = "cities"
files = ["a.geojson", "b.geojson"]
color = [4, 5, 6]
line_width = 2
marker_radius = 7
opacity = 100
font_size = 12
''')
        overlays = gw.load_overlays_config(p)
        assert len(overlays.geojson_sources) == 1
        source = overlays.geojson_sources[0]
        assert isinstance(source, gw.GeoJSONSource)
        assert source.name == "cities"
        assert source.files == ("a.geojson", "b.geojson")
        assert source.color == (4, 5, 6)

    def test_multiple_geojson_sources(self, tmp_path):
        p = write_toml(tmp_path, '''
[[geojson_sources]]
name = "cities"
files = ["cities.geojson"]

[[geojson_sources]]
name = "counties"
files = ["counties.geojson"]
''')
        overlays = gw.load_overlays_config(p)
        assert [s.name for s in overlays.geojson_sources] == ["cities", "counties"]

    def test_unknown_geojson_source_key_raises(self, tmp_path):
        p = write_toml(tmp_path, '''
[[geojson_sources]]
name = "cities"
bogus_key = 1
''')
        with pytest.raises(ValueError, match=r"geojson_sources\[0\]"):
            gw.load_overlays_config(p)

    def test_shell_sources_parsed_into_dataclasses(self, tmp_path):
        p = write_toml(tmp_path, '''
[[shell_sources]]
name = "storms"
command = ["python", "fetch.py"]
timeout = 5.0
color = [7, 8, 9]
''')
        overlays = gw.load_overlays_config(p)
        assert len(overlays.shell_sources) == 1
        source = overlays.shell_sources[0]
        assert isinstance(source, gw.ShellSource)
        assert source.name == "storms"
        assert source.command == ("python", "fetch.py")
        assert source.color == (7, 8, 9)

    def test_unknown_shell_source_key_raises(self, tmp_path):
        p = write_toml(tmp_path, '''
[[shell_sources]]
name = "storms"
bogus_key = 1
''')
        with pytest.raises(ValueError, match=r"shell_sources\[0\]"):
            gw.load_overlays_config(p)


class TestValidateOverlaysConfig:
    def test_empty_is_valid(self):
        gw.validate_overlays_config(gw.OverlaysConfig())  # no raise

    def test_unique_geojson_source_names_pass(self):
        overlays = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="a"), gw.GeoJSONSource(name="b")))
        gw.validate_overlays_config(overlays)  # no raise

    def test_duplicate_geojson_source_names_raise(self):
        overlays = gw.OverlaysConfig(geojson_sources=(gw.GeoJSONSource(name="a"), gw.GeoJSONSource(name="a")))
        with pytest.raises(ValueError, match="geojson_sources names must be unique"):
            gw.validate_overlays_config(overlays)

    def test_unique_shell_source_names_pass(self):
        overlays = gw.OverlaysConfig(shell_sources=(gw.ShellSource(name="a"), gw.ShellSource(name="b")))
        gw.validate_overlays_config(overlays)  # no raise

    def test_duplicate_shell_source_names_raise(self):
        overlays = gw.OverlaysConfig(shell_sources=(gw.ShellSource(name="a"), gw.ShellSource(name="a")))
        with pytest.raises(ValueError, match="shell_sources names must be unique"):
            gw.validate_overlays_config(overlays)

    def test_same_name_across_geojson_and_shell_is_fine(self):
        overlays = gw.OverlaysConfig(
            geojson_sources=(gw.GeoJSONSource(name="storms"),),
            shell_sources=(gw.ShellSource(name="storms"),),
        )
        gw.validate_overlays_config(overlays)  # no raise -- separate namespaces
