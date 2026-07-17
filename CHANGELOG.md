# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `overlay_geojson_files` — a list of local GeoJSON files (state/county borders, a
  coastline layer, a fixed marker set) drawn as georeferenced overlays alongside
  `overlay_graticule`/`overlay_cities`. Supports `Point`/`MultiPoint`/`LineString`/
  `MultiLineString`/`Polygon`/`MultiPolygon`, with per-feature `properties.color`
  overrides accepting an `[r, g, b]` list, a hex string, or any PIL named color (so
  GeoJSON from geojson.io/GitHub's simplestyle-spec works as-is). The composited
  layer is cached in `data_dir`, keyed on each file's path/mtime plus
  satellite/resolution/style, so an unchanged config only pays the parse/project/draw
  cost once instead of every cycle.
- `overlay_shell_command` — an external command (argv list, no shell parsing) run
  once per cycle whose stdout is parsed as GeoJSON and drawn the same way as
  `overlay_geojson_files`, but never cached, for genuinely fresh data (live storm
  tracks, fire perimeters, etc.). A non-zero exit code, timeout, or unparseable
  stdout is logged and skipped rather than breaking the update cycle.
- Point/MultiPoint features from either provider above can carry a `properties.name`
  to draw a text label next to the marker, matching how `overlay_cities` labels a
  city.

## [2.0.0] — 2026-07-16 — full modernization

A ground-up rewrite of the original single-file script. Highlights:

### Added
- TOML config (`config.toml`, `config.example.toml`) with full CLI-flag overrides.
- Retries with backoff on transient failures/HTTP 5xx/429.
- Conditional requests (ETag/`If-None-Match`) so an unchanged frame doesn't get
  reprocessed or reapplied.
- Freshness-aware scheduling: learns *when within each interval* NOAA actually
  publishes a new frame and schedules around that (`sync_to_capture_time`), with an
  in-cycle retry backstop (`wait_for_fresh_capture`) and a pre-fetch sleep mode for
  Task Scheduler-style single-shot use (`--wait-for-sync`).
- Screen-exact cropping (Lanczos cover-crop, avoids Windows' own lower-quality
  scaling), with automatic non-interactive-session screen-size recovery via WMI.
- Info bar overlay with capture metadata, baked into EXIF; automatic taskbar-height
  detection so it doesn't get clipped.
- Region-of-interest source cropping (`source_crop_*`), independent of the
  screen-fit crop.
- Multi-source combos: `combo_mode = "rotate"` (cycle sources over time) and
  `"per_monitor"` (a genuinely different image per physical monitor, not one image
  spanned/tiled).
- Georeferenced overlays (`overlay_graticule`, `overlay_cities`) — real lon/lat
  projected accurately onto CONUS frames, calibrated against real ABI L1b data and
  validated against known city landmarks.
- Power/network-aware fallbacks (`skip_on_battery`, `metered_resolution`).
- Cross-platform backend abstraction (`platform_base.WallpaperPlatform`,
  `platform_windows.WindowsPlatform`) — every OS-specific operation moved behind one
  interface, so `goes_wallpaper.py` itself has no platform-specific code.
- Test suite: 75 `pytest` tests, no network or real hardware required.
- Packaging: installable via `uv build`/`pip install .`, with `goes-wallpaper`
  (console) and `goes-wallpaperw` (windowed) entry points.
- `ATTRIBUTION.md`, `CONTRIBUTING.md`, this changelog.

### Changed
- Rewritten for modern Python 3.11+ (`pathlib`, `slots=True` dataclasses, full type
  hints, `tomllib`).
- Default resolution raised from `2500x1500` to `5000x3000` (covers a 4K monitor
  without upsampling after a crop — NOAA serves several discrete sizes per sector,
  not an arbitrary resize).
- Relicensed from Apache-2.0 to GPL-3.0-or-later; see `ATTRIBUTION.md` for the
  preserved original notice.
- Wallpaper application now sets the `WallpaperStyle`/`TileWallpaper` registry values
  before applying, so the configured style is actually honored (the original always
  relied on whatever style was last set manually).

### Fixed
- NOAA's baked-in caption strip is trimmed before cropping/overlay instead of being
  left to be randomly kept or half-cut by the screen-fit crop.
- The EXIF `Software` tag no longer references a file (`goes_wallpaper.pyw`) that no
  longer exists in the project.

## [1.0.0] — 2020-06-10

Original version, by Paul H (`pjlhjr/GOES-Wallpaper`, Apache License 2.0): a single
script fetching a CONUS GEOCOLOR image from NOAA and setting it as the Windows
wallpaper via `SystemParametersInfoW`, intended to be run periodically via Windows
Task Scheduler.
