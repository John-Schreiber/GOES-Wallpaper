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
# fill = [80, 80, 200]     # Polygon/MultiPolygon fill; unset (default) = outline only
# fill_opacity = 120       # 0-255, only used when fill is set
# icon = "marker"          # a bundled Maki icon name, or a path to your own PNG --
#                          # replaces the outlined circle for Point/MultiPoint features
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

Set `overlay_cache = false` (or pass `--no-overlay-cache`) to skip checking for
an existing match entirely and force a fresh render every cycle — useful while
iterating on styling/icons and wanting to see every change immediately, rather
than reasoning about whether the cache key actually picked it up. A fresh cache
pair is still written afterward, so reuse resumes normally once this is back at
its default (`true`).

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
# fill = [200, 60, 60]
# fill_opacity = 120
# icon = "danger"          # a bundled Maki icon name -- see the full list below
```

Also repeatable, but never cached — the point of shelling out is presumably
fresh data (live storm tracks, fire perimeters). A non-zero exit code, a
timeout, or unparseable stdout is logged and skipped rather than breaking the
update cycle; one broken source doesn't block others.

Two real, working examples ship in `overlays/`:
[`fetch_earthquakes.py`](overlays/fetch_earthquakes.py) fetches USGS's public
earthquake GeoJSON feed and adds `marker-size`/`marker-color` from each quake's
magnitude; [`location/`](overlays/location) has one "you are here" script per
OS (Windows/macOS/Linux), each querying that platform's own geolocation
service — see [`overlays/location/README.md`](overlays/location/README.md)
before using one (each needs a small extra install, and none has been tested
against real hardware yet — see its own docstring for exactly what's
unconfirmed).

**Security note:** `command` is a code-execution surface by design — it runs
whatever argv you configure, every cycle. That's the feature working as
intended (there's no sandboxing), but it means `overlays.toml` (like
`config.toml`) must never be pointed at an untrusted file via `--config`/
`--overlays-config`, and neither file should be writable by less-privileged
users than whoever runs `goes_wallpaper`.

## GeoJSON styling rules

Both `geojson_sources` and `shell_sources` draw through the same shared code
(`_build_geojson_layer`), so they're styled identically. Rendering goes through
[aggdraw](https://github.com/pytroll/aggdraw) (Anti-Grain Geometry), not raw
`PIL.ImageDraw` — strokes, fills, and marker circles are all anti-aliased,
matching the info block/EXIF text and everything else drawn on the frame.

* **Geometry type decides the draw call.** `Point`/`MultiPoint` → an outlined
  circle (`marker_radius`, stroke `line_width`) or a pasted icon (see below).
  `LineString`/`MultiLineString` → an open polyline. `Polygon`/`MultiPolygon`
  → each ring stroked, and filled if `fill` resolves to something (an entry's
  own `fill` config, or a feature's `properties.fill`) — interior rings render
  as real holes (e.g. a country polygon with a lake cut out), not a solid
  block, as long as every ring's vertices project onto the frame; a ring that
  partly falls off-frame falls back to outline-only for that polygon (a
  filled ring has no equivalent to a stroked line's "break at the edge").
  Any other/missing geometry type is silently skipped, not an error.
* **Custom icons for points.** Set `icon` (an entry-wide default) and/or a
  feature's `properties.icon` to either a name from the bundled
  [Maki](https://github.com/mapbox/maki) icon set (CC0-1.0; run
  `ls overlays/icons` for the full list of ~215 names, e.g. `"marker"`,
  `"star"`, `"fire-station"`, `"hospital"`) or a path to your own PNG — either
  replaces the outlined circle entirely for that feature (the label still
  draws next to it). A bundled icon is a recolorable silhouette and gets
  tinted with the feature's resolved marker/stroke color; a custom PNG keeps
  its own colors untouched. `properties.marker-symbol` (the real
  simplestyle-spec field) also resolves against the bundled set only, not an
  arbitrary path — `icon`/`properties.icon` is this project's broader
  extension and takes precedence when both are set on the same feature. An
  unrecognized name/path is logged and falls back to the outlined circle.
* **`Point`/`MultiPoint` get a text label from `properties.name`**, drawn next
  to the marker or icon (`font_size`, using `info_font_path`, falling back to a
  built-in font). No `name` means no label. A `MultiPoint`'s single `name` is
  drawn next to every point in it. `LineString`/`Polygon` ignore
  `properties.name` — no single anchor point to label.
* **Per-feature style overrides prefer [simplestyle-spec](https://github.com/mapbox/simplestyle-spec)
  property names**, with this project's older ad hoc names still honored as a
  fallback so existing GeoJSON keeps working unchanged:
  | Concern | simplestyle name | legacy fallback | entry-level config |
  |---|---|---|---|
  | Stroke color | `stroke` | `color` | `color` |
  | Stroke width | `stroke-width` | — | `line_width` |
  | Stroke opacity | `stroke-opacity` (0.0–1.0) | — | `opacity` (0–255) |
  | Fill color | `fill` | — | `fill` |
  | Fill opacity | `fill-opacity` (0.0–1.0) | — | `fill_opacity` (0–255) |
  | Marker color (points) | `marker-color` | `stroke`, then `color` | `color` |
  | Marker size (points) | `marker-size` (`"small"`/`"medium"`/`"large"` → 0.6×/1.0×/1.6×) | — | `marker_radius` |

  Colors accept `[r, g, b]`, a hex string, or any of PIL's ~140 named colors,
  so GeoJSON from common tools (geojson.io, simplestyle-spec exporters) works
  as-is. An unparseable value falls back to the entry's config (logged) rather
  than losing the whole overlay. Font size always comes from the entry's
  config, not per-feature — simplestyle has no equivalent field.
* **Line width and marker radius scale with output resolution**, like
  `[graticule]` — tuned for a ~2000px-wide frame, scaling proportionally at
  higher `resolution`. A `properties.stroke-width` override scales the same
  way; `properties.marker-size` is a multiplier on top of the already-scaled
  `marker_radius`.
* **A point/vertex projecting outside the visible frame breaks the line/ring
  there** rather than drawing a stray edge across the image, for strokes and
  unfilled polygons. A `Polygon` with a corner just outside the frame renders
  as an open outline missing the two edges at that corner, not a
  rubber-banded line back across the image — see the fill note above for how
  a filled polygon differs.
