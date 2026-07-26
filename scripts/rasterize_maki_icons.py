#!/usr/bin/env python3
# scripts/rasterize_maki_icons.py -- one-time (re-run-as-needed) conversion of the
# vendored Maki SVG icon set (vendor/maki/icons/) into the PNGs goes_wallpaper.py
# actually reads at runtime (overlays/icons/). Not wired into the main CLI,
# runtime dependencies, or the project's tracked dependency groups -- this
# script's own deps (svglib, reportlab, rlPyCairo) pull in pycairo, which has no
# Linux wheel and fails to build on a bare ubuntu-latest CI runner (no system
# libcairo/pkg-config). Since nothing at runtime ever needs to render SVG, and
# this script is only ever run ad hoc on a dev machine when re-vendoring icons,
# install its deps by hand in a scratch venv instead of via `uv sync`:
#     uv run --with svglib --with reportlab --with rlpycairo python scripts/rasterize_maki_icons.py
#
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rasterize every vendor/maki/icons/*.svg into a same-named PNG under
overlays/icons/, at a fixed TARGET_SIZE square canvas (each source SVG is
scaled up/down uniformly to fit, aspect preserved, transparent background).
See vendor/maki/README.md for provenance/license (CC0-1.0) and re-vendoring
instructions.

Usage:
    uv run --with svglib --with reportlab --with rlpycairo python scripts/rasterize_maki_icons.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

TARGET_SIZE = 128  # px, square canvas every icon is scaled to fit within

_VENDOR_DIR = Path(__file__).with_name("..").resolve() / "vendor" / "maki" / "icons"
_OUTPUT_DIR = Path(__file__).with_name("..").resolve() / "overlays" / "icons"


def rasterize(svg_path: Path, output_path: Path) -> None:
    drawing = svg2rlg(str(svg_path))
    scale = TARGET_SIZE / max(drawing.width, drawing.height)
    drawing.width *= scale
    drawing.height *= scale
    drawing.scale(scale, scale)
    # backendFmt="RGBA" + bg=None -> a genuinely transparent canvas (not just a
    # flat color to chroma-key out), with real anti-aliased alpha at the shape's
    # edges -- confirmed by inspecting the rendered alpha channel directly.
    image = renderPM.drawToPIL(drawing, backendFmt="RGBA", bg=None, dpi=72)
    image.save(output_path)


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_paths = sorted(_VENDOR_DIR.glob("*.svg"))
    if not svg_paths:
        raise SystemExit(f"No SVGs found under {_VENDOR_DIR} -- see vendor/maki/README.md")
    for svg_path in svg_paths:
        rasterize(svg_path, _OUTPUT_DIR / f"{svg_path.stem}.png")
    print(f"Rasterized {len(svg_paths)} icon(s) from {_VENDOR_DIR} to {_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
