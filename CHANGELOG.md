# Changelog

Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [2.1.0] — 2026-07-17 — GeoJSON overlays and a raw-data source

### Added
- `overlay_geojson_files` — a list of local GeoJSON files (state/county borders, a
  coastline layer, a fixed marker set) drawn as georeferenced overlays alongside
  `overlay_graticule`/`overlay_cities`. Supports `Point`/`MultiPoint`/`LineString`/
  `MultiLineString`/`Polygon`/`MultiPolygon`, with per-feature `properties.color`
  overrides accepting an `[r, g, b]` list, a hex string, or any PIL named color (so
  GeoJSON from geojson.io/GitHub's simplestyle-spec works as-is). The composited
  layer is cached in `data_dir` as its own file per distinct (files, satellite, frame
  size, style) combination, so combos spanning more than one satellite/resolution
  each get their own cache entry instead of invalidating and overwriting each other's
  every cycle — an unchanged config only pays the parse/project/draw cost once.
- `overlay_shell_command` — an external command (argv list, no shell parsing) run
  once per cycle whose stdout is parsed as GeoJSON and drawn the same way as
  `overlay_geojson_files`, but never cached, for genuinely fresh data (live storm
  tracks, fire perimeters, etc.). A non-zero exit code, timeout, or unparseable
  stdout is logged and skipped rather than breaking the update cycle.
- Point/MultiPoint features from either provider above can carry a `properties.name`
  to draw a text label next to the marker, matching how `overlay_cities` labels a
  city.
- `source_kind = "satpy_raw"` — an opt-in source (behind the new `satpy-raw` install
  extra) that fetches raw ABI L1b radiance bands directly from the public
  `noaa-goes16`/`noaa-goes18`/`noaa-goes19` S3 buckets and composites a GeoColor-style
  image locally with [satpy](https://satpy.readthedocs.io/), instead of fetching NOAA
  STAR's pre-rendered JPG. No baked-in state lines/logo/fake city lights, and exposes
  real projection/area info so georeferenced overlays work accurately on Full Disk and
  Mesoscale sectors too, not just CONUS. Builds its own day/night blend — true-color by
  day, a muted navy-to-pale-lavender Band 13 brightness-temperature mapping by night,
  blended at the real per-pixel solar terminator — rather than relying on satpy's stock
  `geo_color` composite. First cut: no automatic fallback to `cdn_jpg` and no
  cross-cycle caching of downloaded bands, so it's meaningfully heavier on
  bandwidth/compute — see the README's "Custom raw-data source (satpy_raw)" section
  before enabling it on a tight `interval_minutes`.

### Fixed
- `source_kind = "satpy_raw"` leaked ~98MB (CONUS) / ~550MB (Full Disk) of raw band
  files into `<data_dir>/satpy_raw_cache` every cycle, since each scan's filenames
  are unique and nothing ever deleted the previous cycle's files — a 5-minute
  `--loop` could leak tens of GB/day. `fetch_composite` now clears everything not
  part of the current scan's selection before downloading it.
- The info bar's satellite/sector/product text and capture-time text could overlap
  and render as garbled, overlapping text — most visible on `satpy_raw`'s longer
  product label (`GeoColor (satpy_raw)`) on a square Full Disk frame, where the bar
  is proportionally taller relative to the available width than on a widescreen
  CONUS crop. The font size now shrinks (down to a legibility floor) until both
  texts fit without overlapping.
- `state.json`/`wallpaper.json` (and the GeoJSON overlay cache sidecar) were
  written with a bare `write_text`, so a crash or power loss mid-write could corrupt
  them — silently discarding every learned publish-time phase and ETag on next
  load. Both now write through a same-directory temp file + atomic `os.replace`.
- Full Disk's largest tier (`10848x10848`, ~117.7M pixels) exceeded Pillow's default
  `MAX_IMAGE_PIXELS` (~89.5M), logging a `DecompressionBombWarning` on every such
  fetch. Raised to a bounded 130M that covers every known NOAA tier, rather than
  disabled outright — the guard still does real work against a compromised or
  misbehaving CDN.
- `Ctrl-C` during `--loop` exited with a raw traceback (`KeyboardInterrupt` is a
  `BaseException`, so it skipped both `run_loop`'s and `main()`'s `except
  Exception` handlers). `main()` now catches it directly for a clean exit.
- `data_dir`/`info_font_path` defaults were hardcoded to Windows paths
  (`~/AppData/Local/...`, `C:\Windows\Fonts\...`) directly in the otherwise
  cross-platform `Config`/`load_config`. `WallpaperPlatform` gained
  `default_data_dir()`/`default_font_path()`, which `load_config` now prefers when
  neither config.toml nor a CLI override sets them — a prerequisite for a future
  Linux/macOS backend, which only needs to implement those two methods.

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
