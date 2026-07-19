# Georeferenced overlays

Content drawn on top of the fetched satellite image — a lat/lon grid, labeled
markers, borders, storm tracks, anything expressible as GeoJSON — configured
separately from `config.toml`, in `overlays.toml` (override the path with
`--overlays-config path/to/other.toml`). A missing file means no overlays, same
as an all-empty one. See [overlays.toml](overlays.toml) for a live example
(city markers) plus more commented-out ones.

Overlays live in their own file because they're content (what to draw), not app
behavior (how to fetch/crop/schedule), and grow independently — more cities,
more GeoJSON layers, without touching a behavior setting. Have a pre-2.3.0
`config.toml` with flat `overlay_*` keys or `[[overlay_cities]]`? Run
`migrate_overlay_config.py` to convert it automatically.

Real georeferencing, not eyeballed: `lonlat_to_pixels()` projects lon/lat into
the image's actual GEOS satellite projection via `pyproj`. The CONUS extent for
each satellite was derived from a real ABI L1b radiance file and validated
against 10 known city landmarks (median error well under a pixel at
2500×1500). The Full Disk extent is reused directly from satpy's own shipped
area definitions, cross-checked in `tests/test_geolocation.py` against an
independent `pyresample` computation.

**CONUS and Full Disk only** (for the default `cdn_jpg` source) — Mesoscale
sectors move, so their extent can't be hardcoded. An overlay on a Mesoscale
sector logs a warning and skips drawing rather than rendering something
misplaced. With `source_kind = "satpy_raw"` (see
[README.md](README.md#custom-raw-data-source-satpy_raw)), overlays work on any
sector via that source's real per-frame georeferencing.

This adds content on top — it doesn't remove NOAA's own baked-in state
lines/logo for `cdn_jpg` (see README's [Source image
caveats](README.md#source-image-caveats)). `satpy_raw` has no baked-in
annotations to begin with.

Marker/line sizes are tuned for a ~2000px-wide frame and scale up automatically
at higher `resolution` settings. If `output_projection` (see
[README.md](README.md#output-projection)) is set to anything other than
`"native"`, overlays are drawn *before* reprojection and get warped along with
the base image — see [PROJECTIONS.md](PROJECTIONS.md)'s known limitations.

## `[graticule]`

A lat/lon grid, the one procedural (non-GeoJSON) overlay — computed from
`step_deg`, not authored content.

```toml
[graticule]
enabled = false
step_deg = 10.0
color = [255, 255, 0]
opacity = 110    # 0-255
```

## `[[geojson_sources]]` — static files, cached

Everything else — including city markers — is just GeoJSON. There's no
separate "city" concept in code: a labeled city is a `Point` feature with a
`name` property, drawn through the same path as any other static content.

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
independently named and styled (city markers, county borders, a coastline
layer), composited in order. `files` merges every listed file's features
before drawing.

Each entry's composited RGBA layer is cached in `data_dir` as
`overlay_geojson_cache_<id>.png` + a `.json` sidecar, `<id>` a short hash of
that entry's name/files/satellite/frame size/style — so entries never collide.
Staleness checks each file's path and modification time plus
name/satellite/resolution/style, so an unchanged config only pays the
parse/project/draw cost once. Removing/renaming a source, or changing its
satellite/resolution/style, mints a new cache identity rather than reusing the
old one — the orphaned pair is deleted automatically once it's gone unused for
`overlay_cache_max_age_days` (30 by default; 0 disables this). Every cache hit
touches both files' timestamps, so an entry still in active use is never
pruned no matter how old its content is.

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
shell string, so no shell-injection risk) run fresh every cycle, expected to
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

Also repeatable, but never cached — the point of shelling out is presumably
fresh data (live storm tracks, fire perimeters). A non-zero exit code, a
timeout, or unparseable stdout is logged and skipped rather than breaking the
update cycle; one broken source doesn't block others.

**Security note:** `command` is a code-execution surface by design — it runs
whatever argv you configure, every cycle. That's the feature working as
intended (there's no sandboxing), but it means `overlays.toml` (like
`config.toml`) must never be pointed at an untrusted file via `--config`/
`--overlays-config`, and neither file should be writable by less-privileged
users than whoever runs `goes_wallpaper`.

## GeoJSON styling rules

Both `geojson_sources` and `shell_sources` draw through the same shared code
(`_build_geojson_layer`), so they're styled identically:

* **Geometry type decides the draw call.** `Point`/`MultiPoint` → an outlined
  circle (`marker_radius`, stroke `line_width`). `LineString`/
  `MultiLineString` → an open polyline. `Polygon`/`MultiPolygon` → each ring
  as a *closed, outlined* loop — **not filled**, no fill-color config. Any
  other/missing geometry type is silently skipped, not an error.
* **`Point`/`MultiPoint` get a text label from `properties.name`**, drawn next
  to the marker (`font_size`, using `info_font_path`, falling back to a
  built-in font). No `name` means no label. A `MultiPoint`'s single `name` is
  drawn next to every point in it. `LineString`/`Polygon` ignore
  `properties.name` — no single anchor point to label.
* **Only color and (for points) the label are overridable per feature.** A
  feature's `properties.color` replaces the entry's `color` for that feature —
  accepts `[r, g, b]`, a hex string, or any of PIL's ~140 named colors, so
  GeoJSON from common tools (geojson.io, GitHub's simplestyle-spec) works
  as-is. An unparseable value falls back to the entry's `color` (logged)
  rather than losing the whole overlay. Line width, marker radius, opacity,
  and font size always come from the entry's config, not per-feature.
* **Opacity** is a single alpha value (0–255) applied uniformly to every
  feature in that entry, not adjustable per feature.
* **Line width and marker radius scale with output resolution**, like
  `[graticule]` — tuned for a ~2000px-wide frame, scaling proportionally at
  higher `resolution`.
* **A point/vertex projecting outside the visible frame breaks the line/ring
  there** rather than drawing a stray edge across the image. A `Polygon` with
  a corner just outside the frame renders as an open outline missing the two
  edges at that corner, not a rubber-banded line back across the image.
