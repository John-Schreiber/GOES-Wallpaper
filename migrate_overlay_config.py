#!/usr/bin/env python3
# migrate_overlay_config.py -- one-time migration from the pre-2.3.0 flat overlay_*
# config.toml keys to the new overlays.toml (see OVERLAYS.md). Not wired into the
# main goes_wallpaper.py CLI -- this is a run-once tool, not a permanent feature.
#
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Migrate a pre-2.3.0 config.toml (flat overlay_* keys, [[overlay_cities]]) to the
2.3.0+ split format: a new overlays.toml (graticule/geojson_sources/shell_sources)
plus an overlays/cities.geojson if overlay_cities was set, and a cleaned config.toml
with the overlay_* keys removed.

Usage:
    uv run python migrate_overlay_config.py path/to/config.toml

Never overwrites without a backup: the original config.toml is copied to
config.toml.bak before being replaced. overlays.toml and overlays/cities.geojson are
refused if they already exist (delete/move them first if you want to re-run this).
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

_OVERLAY_KEY_RE = re.compile(r"^overlay_\w+\s*=")
_SECTION_HEADER_RE = re.compile(r"^# --- ")

# Verbatim markers for the two comment blocks this repo's own shipped config.toml
# uses -- stripped if found, left alone otherwise (a differently-worded config.toml
# just keeps a vestigial comment or two; not worth generalizing comment-parsing for
# a one-time migration).
_OVERLAY_SECTION_MARKER = "# --- Georeferenced overlays (optional) ---"
_CITY_MARKERS_SECTION_MARKER = "# --- City markers"


def _strip_overlay_lines(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    in_overlay_comment_block = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == _OVERLAY_SECTION_MARKER or stripped.startswith(_CITY_MARKERS_SECTION_MARKER):
            in_overlay_comment_block = True
            i += 1
            continue
        if in_overlay_comment_block:
            if stripped.startswith("#") or stripped == "":
                i += 1
                continue
            in_overlay_comment_block = False
            # fall through, re-process this line normally

        if _OVERLAY_KEY_RE.match(stripped):
            i += 1
            continue

        if stripped == "[[overlay_cities]]":
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("#", "[")):
                i += 1
            continue

        out.append(line)
        i += 1

    # Collapse any run of 3+ blank lines left behind by removed blocks down to 2 (one
    # blank separator), and trailing blank lines at EOF down to a single newline.
    text = "".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip("\n") + "\n"


def _format_toml_array(items: list) -> str:
    return "[" + ", ".join(json.dumps(v) for v in items) + "]"


def migrate(config_path: Path) -> None:
    raw_text = config_path.read_text()
    with config_path.open("rb") as f:
        values = tomllib.load(f)

    overlay_keys = {k: v for k, v in values.items() if k.startswith("overlay_")}
    overlay_cities = values.get("overlay_cities", [])

    if not overlay_keys and not overlay_cities:
        print(f"{config_path}: no overlay_* keys or [[overlay_cities]] found -- nothing to migrate.")
        return

    out_dir = config_path.parent
    overlays_toml_path = out_dir / "overlays.toml"
    cities_geojson_path = out_dir / "overlays" / "cities.geojson"

    if overlays_toml_path.exists():
        raise SystemExit(f"{overlays_toml_path} already exists -- move it aside before re-running this.")
    if overlay_cities and cities_geojson_path.exists():
        raise SystemExit(f"{cities_geojson_path} already exists -- move it aside before re-running this.")

    toml_parts: list[str] = [
        "# Migrated from config.toml's overlay_* keys by migrate_overlay_config.py.\n"
        "# See OVERLAYS.md for the full schema.\n",
    ]

    graticule_enabled = overlay_keys.get("overlay_graticule", False)
    graticule_step = overlay_keys.get("overlay_graticule_step_deg", 10.0)
    graticule_color = overlay_keys.get("overlay_graticule_color", [255, 255, 0])
    graticule_opacity = overlay_keys.get("overlay_graticule_opacity", 110)
    toml_parts.append(
        f"[graticule]\n"
        f"enabled = {str(graticule_enabled).lower()}\n"
        f"step_deg = {graticule_step}\n"
        f"color = {_format_toml_array(graticule_color)}\n"
        f"opacity = {graticule_opacity}\n"
    )

    moved: list[str] = []
    sources_written = 0

    if overlay_cities:
        cities_geojson_path.parent.mkdir(parents=True, exist_ok=True)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                "properties": {"name": c["name"]},
            }
            for c in overlay_cities
        ]
        cities_geojson_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n")
        rel_path = f"overlays/{cities_geojson_path.name}"
        toml_parts.append(
            f'\n[[geojson_sources]]\n'
            f'name = "cities"\n'
            f'files = {_format_toml_array([rel_path])}\n'
            f'color = {_format_toml_array(overlay_keys.get("overlay_city_color", [255, 60, 60]))}\n'
            f'marker_radius = {overlay_keys.get("overlay_city_marker_radius", 5)}\n'
            f'font_size = {overlay_keys.get("overlay_city_font_size", 18)}\n'
        )
        moved.append(f"{len(overlay_cities)} overlay_cities entries -> {cities_geojson_path} (geojson_sources[\"cities\"])")
        sources_written += 1

    if overlay_keys.get("overlay_geojson_files"):
        toml_parts.append(
            f'\n[[geojson_sources]]\n'
            f'name = "default"\n'
            f'files = {_format_toml_array(overlay_keys["overlay_geojson_files"])}\n'
            f'color = {_format_toml_array(overlay_keys.get("overlay_geojson_color", [255, 255, 255]))}\n'
            f'line_width = {overlay_keys.get("overlay_geojson_line_width", 1)}\n'
            f'marker_radius = {overlay_keys.get("overlay_geojson_marker_radius", 5)}\n'
            f'opacity = {overlay_keys.get("overlay_geojson_opacity", 160)}\n'
            f'font_size = {overlay_keys.get("overlay_geojson_font_size", 14)}\n'
        )
        moved.append('overlay_geojson_files -> geojson_sources["default"]')
        sources_written += 1

    if overlay_keys.get("overlay_shell_command"):
        toml_parts.append(
            f'\n[[shell_sources]]\n'
            f'name = "default"\n'
            f'command = {_format_toml_array(overlay_keys["overlay_shell_command"])}\n'
            f'timeout = {overlay_keys.get("overlay_shell_timeout", 10.0)}\n'
            f'color = {_format_toml_array(overlay_keys.get("overlay_shell_color", [0, 200, 255]))}\n'
            f'line_width = {overlay_keys.get("overlay_shell_line_width", 2)}\n'
            f'marker_radius = {overlay_keys.get("overlay_shell_marker_radius", 5)}\n'
            f'opacity = {overlay_keys.get("overlay_shell_opacity", 200)}\n'
            f'font_size = {overlay_keys.get("overlay_shell_font_size", 14)}\n'
        )
        moved.append('overlay_shell_command -> shell_sources["default"]')
        sources_written += 1

    overlays_toml_path.write_text("".join(toml_parts))

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    if backup_path.exists():
        raise SystemExit(f"{backup_path} already exists -- move it aside before re-running this.")
    backup_path.write_text(raw_text)
    config_path.write_text(_strip_overlay_lines(raw_text))

    print(f"Backed up original to {backup_path}")
    print(f"Wrote {overlays_toml_path} ({sources_written} source(s) + graticule)")
    for m in moved:
        print(f"  - {m}")
    print(f"Rewrote {config_path} with overlay_* keys removed")
    print("\nReview both files by hand -- this migration doesn't preserve every "
          "comment from the original config.toml, and any overlay-related prose "
          "elsewhere in the file (cross-references in other sections' comments, "
          "for example) is left as-is.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} path/to/config.toml")
    migrate(Path(sys.argv[1]))
