# Georeferenced overlays

Content drawn on top of the fetched satellite image — a lat/lon grid, labeled
markers, borders, storm tracks, anything expressible as GeoJSON — configured
separately from `config.toml`, in `overlays.toml` (override the path with
`--overlays-config path/to/other.toml`). A missing file means no overlays, same as
an all-empty one. See [overlays.toml](overlays.toml) for a live example (city
markers) and more commented-out ones.

Overlays live in their own file rather than `config.toml` because they're content
(what to draw), not app behavior (how to fetch/crop/schedule) — and content grows
independently of everything else: more cities, more GeoJSON layers, without
touching a single behavior setting. If you have a pre-2.3.0 `config.toml` with flat
`overlay_*` keys and/or `[[overlay_cities]]`, run `migrate_overlay_config.py` to
convert it automatically — see that script's docstring.

Real georeferencing, not eyeballed: `lonlat_to_pixels()` projects lon/lat into the
image's actual GEOS satellite projection using `pyproj`. The CONUS extent for each
satellite was derived from a real ABI L1b radiance file (loaded with `satpy` during
development, not a runtime dependency) and validated against 10 known city
landmarks — median error well under a pixel at 2500×1500. The Full Disk extent is
reused directly from `satpy`'s own shipped area definitions
(`goes_west`/`east_abi_f_2km`), since Full Disk's fixed viewing geometry is
identical for every GOES-R series satellite regardless of orbital slot — and
cross-checked in `tests/test_geolocation.py` against an independent `pyresample`
computation over the same area.

**CONUS and Full Disk only** (for the default `cdn_jpg` source). Mesoscale sectors
move (NOAA repositions them), so their extent can't be hardcoded the same way.
Enabling an overlay on a Mesoscale sector logs a warning and skips drawing rather
than rendering something misplaced. With `source_kind = "satpy_raw"` (see
[README.md](README.md#custom-raw-data-source-satpy_raw)), overlays work on any
sector, including Mesoscale, via that source's real per-frame georeferencing.

This adds content on top — it doesn't and can't remove NOAA's own baked-in state
lines/logo for the default `cdn_jpg` source_kind (see README.md's "Source image
caveats" for why). `satpy_raw` has no baked-in annotations to begin with, since
it's composited from raw bands rather than fetching NOAA's pre-rendered JPG.

Marker/line sizes are tuned for a ~2000px-wide frame (`_OVERLAY_REFERENCE_WIDTH_PX`
in `goes_wallpaper.py`) and scale up automatically at higher `resolution` settings.

Reprojection quality note: if `output_projection` (see
[README.md](README.md#output-projection)) is set to anything other than `"native"`,
overlays are drawn *before* reprojection and get warped along with the base image —
see [PROJECTIONS.md](PROJECTIONS.md)'s "Known quality limitations" section.

## `[graticule]`

A lat/lon grid, the one procedural (non-GeoJSON) overlay — computed from `step_deg`,
not authored content, so it doesn't fit the GeoJSON-file model below.

```toml
[graticule]
enabled = false
step_deg = 10.0
color = [255, 255, 0]
opacity = 110    # 0-255
```

## `[[geojson_sources]]` — static files, cached

Everything else — including city markers — is just GeoJSON. There's no separate
"city" concept in code: a labeled city is a `Point` feature with a `name` property,
drawn through the same path as any other static GeoJSON content.

```toml
[[geojson_sources]]
name = "cities"                          # unique; used in cache filenames and log lines
files = ["overlays/cities.geojson"]
color = [255, 60, 60]
line_width = 1
marker_radius = 5
opacity = 160    # 0-255
font_size = 14
```

Repeatable — add as many `[[geojson_sources]]` blocks as you want, each
independently named and styled (e.g. one for city markers, one for county borders,
one for a coastline layer), composited in the order they appear. `files` merges
every listed file's features before drawing.

The composited RGBA layer for each entry is cached in `data_dir` as
`overlay_geojson_cache_<id>.png` + a matching `.json` sidecar, where `<id>` is a
short hash of that entry's `name`/files/satellite/frame size/style — so two entries
(or the same entry across satellites/resolutions) never collide or overwrite each
other's cache. Staleness is checked on each file's path *and* modification time,
plus name/satellite/resolution/style — an unchanged config only pays the
parse/project/draw cost once; editing a file, bumping `resolution`, or changing any
style field on that entry invalidates just that entry's cache and rebuilds it on the
next cycle. Nothing prunes cache entries for since-removed/renamed sources — they're
simply left behind in `data_dir`.

`overlays/cities.geojson` (the shipped example):

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [-70.2568, 43.6591]},
      "properties": {"name": "Portland, ME"}
    }
  ]
}
```

## `[[shell_sources]]` — a live command, never cached

An external command (an argv list, e.g. `["python", "fetch_storms.py"]` — not a
shell string, so there's no shell-injection risk) run fresh every cycle, expected to
print a GeoJSON `FeatureCollection`/`Feature`/bare geometry to stdout.

```toml
[[shell_sources]]
name = "storm_tracks"
command = ["python", "fetch_storms.py"]
timeout = 10.0
color = [0, 200, 255]
line_width = 2
marker_radius = 5
opacity = 200
font_size = 14
```

Also repeatable. Unlike `geojson_sources`, never cached — the whole point of
shelling out is presumably to pick up genuinely fresh data (live storm tracks, fire
perimeters). A non-zero exit code, a `timeout`, or unparseable stdout is logged and
skipped rather than breaking the update cycle; one broken source doesn't prevent
other `geojson_sources`/`shell_sources` entries from drawing.

## GeoJSON styling rules

Both `geojson_sources` and `shell_sources` draw through the same shared code
(`_build_geojson_layer` in `goes_wallpaper.py`), so they're styled identically:

* **Geometry type decides the draw call.** `Point`/`MultiPoint` → an outlined circle
  (radius = `marker_radius`, stroke width = `line_width`) at each point.
  `LineString`/`MultiLineString` → an open polyline. `Polygon`/`MultiPolygon` → each
  ring drawn as a *closed, outlined* loop — **not filled**; there's no fill color
  config, only stroke. Any other/missing `geometry.type` (e.g. `GeometryCollection`,
  or a feature with no `geometry` at all) is silently skipped, not an error.
* **`Point`/`MultiPoint` features get a text label from `properties.name`**, drawn
  next to the marker (font size: `font_size`, using the shared top-level
  `info_font_path`; falls back to a built-in default font if that path can't be
  loaded). No `name` property means no label — just the marker. A `MultiPoint`'s
  single `name` is drawn next to *every* point in it, since GeoJSON has no way to
  give each point its own name. `LineString`/`Polygon` features ignore
  `properties.name` entirely — there's no single anchor point to draw a label at.
* **Only color and (for points) the label are overridable per feature.** A
  feature's `properties.color` replaces that entry's `color` for that one feature —
  accepts an `[r, g, b]` list, a hex string (`"#ff8800"`), or any of PIL's ~140
  named colors (`"red"`), so GeoJSON exported from common tools (geojson.io,
  GitHub's simplestyle-spec) works as-is without converting colors to lists first.
  A value that doesn't parse as any of those falls back to the entry's `color`
  (logged), rather than raising and losing the whole overlay over one bad feature.
  Handy for e.g. color-coding storm tracks by category, or fire perimeters by
  containment status. Line width, marker radius, opacity, and font size always come
  from the entry's config; there's no `properties.line_width` or similar for those.
* **Opacity is a single alpha value** (`opacity`, 0–255) applied uniformly to every
  feature's fill color in that entry when compositing — not part of `properties`,
  and not adjustable per feature the way color is.
* **Line width and marker radius scale with output resolution**, exactly like
  `[graticule]`: both are tuned for a ~2000px-wide frame
  (`_OVERLAY_REFERENCE_WIDTH_PX`) and scale up proportionally at higher `resolution`
  settings, so a config tuned at one resolution still looks right at another.
* **A point/vertex that projects outside the visible frame breaks the line/ring at
  that point** rather than drawing a stray edge across the image. For a `Polygon`,
  this means a shape with a corner just outside the frame renders as an open
  outline missing the two edges that meet at that corner, not a rubber-banded line
  back across the frame.
