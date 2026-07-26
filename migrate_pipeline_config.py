#!/usr/bin/env python3
# migrate_pipeline_config.py -- one-time migration from the pre-2.4.0 combo_mode/
# [[combos]] config.toml keys to the 2.4.0+ pipeline_mode/[[pipelines]] names (see
# README.md's "Named pipelines" section). Not wired into the main goes_wallpaper.py
# CLI -- this is a run-once tool, not a permanent feature.
#
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Migrate a pre-2.4.0 config.toml (combo_mode, [[combos]]) to the 2.4.0+ names
(pipeline_mode, [[pipelines]]). Pure key rename -- no restructuring, since every
existing Combo field is still valid on Pipeline (Pipeline just adds more optional
ones: output_projection*, wallpaper_style, output_file, output_width,
output_height). Comment lines mentioning combo_mode/combos/[[combos]] are left as
prose (harmless if stale) except for the exact section-header line this repo's own
config.toml uses, which is rewritten to match.

Usage:
    uv run python migrate_pipeline_config.py path/to/config.toml

Never overwrites without a backup: the original config.toml is copied to
config.toml.bak before being replaced.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Matches "combo_mode = ..." at the start of a line (not inside a comment/string).
_COMBO_MODE_RE = re.compile(r"^combo_mode(\s*=)")
_COMBOS_HEADER_RE = re.compile(r"^\[\[combos\]\]")


def migrate(config_path: Path) -> None:
    raw_text = config_path.read_text()
    lines = raw_text.splitlines(keepends=True)

    renamed_mode = 0
    renamed_headers = 0
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if _COMBO_MODE_RE.match(stripped):
            out.append(indent + _COMBO_MODE_RE.sub(r"pipeline_mode\1", stripped))
            renamed_mode += 1
            continue
        if _COMBOS_HEADER_RE.match(stripped):
            out.append(indent + _COMBOS_HEADER_RE.sub("[[pipelines]]", stripped))
            renamed_headers += 1
            continue
        out.append(line)

    if not renamed_mode and not renamed_headers:
        print(f"{config_path}: no combo_mode key or [[combos]] section found -- nothing to migrate.")
        return

    new_text = "".join(out)
    # This repo's own shipped config.toml uses these exact comment strings --
    # harmless to leave stale on a differently-worded config.toml, not worth
    # generalizing comment-parsing for a one-time migration.
    new_text = new_text.replace(
        "# --- Multi-source combos (optional) ---",
        "# --- Named pipelines (optional) ---",
    )
    new_text = new_text.replace(
        "# --- Multi-source combos (array-of-tables; must come after every scalar setting\n"
        "# above -- see the note at the top of this file) ---",
        "# --- Named pipelines (array-of-tables; must come after every scalar setting\n"
        "# above -- see the note at the top of this file) ---",
    )

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    if backup_path.exists():
        raise SystemExit(f"{backup_path} already exists -- move it aside before re-running this.")
    backup_path.write_text(raw_text)
    config_path.write_text(new_text)

    print(f"Backed up original to {backup_path}")
    print(f"Renamed {renamed_mode} combo_mode key(s) to pipeline_mode")
    print(f"Renamed {renamed_headers} [[combos]] header(s) to [[pipelines]]")
    print(
        "\nReview the file by hand -- comment prose elsewhere that still says "
        '"combo"/"combos" (cross-references in other sections\' comments, for '
        "example) is left as-is."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} path/to/config.toml")
    migrate(Path(sys.argv[1]))
