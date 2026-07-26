# Vendored: Mapbox Maki icons

Source: https://github.com/mapbox/maki, commit `28e2a3602e4bde033a1dd388e86e9c117b425e34`
(2025-06-23). License: CC0-1.0 (`LICENSE.txt` in this directory, copied verbatim
from upstream) — public domain, no attribution required.

`icons/*.svg` is the complete upstream `icons/` directory (215 files, ~600KB),
vendored as-is rather than pulled in as a live dependency, since Maki has no
PyPI package and this project only needs a fixed set of small vector source
files, not a live upstream connection.

These are the rasterization source, not what ships at runtime —
`scripts/rasterize_maki_icons.py` converts every SVG here into a PNG under
`overlays/icons/`, which is what `goes_wallpaper.py`'s `_BUNDLED_ICONS`
registry actually reads. Re-run that script (and re-vendor from upstream first,
if picking up new/changed icons) rather than hand-editing the generated PNGs.
