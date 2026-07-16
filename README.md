# GOES Desktop Wallpaper Updater

A Python script that downloads the most recent image from a GOES weather satellite
(NOAA STAR's public CDN) and sets it as the Windows desktop wallpaper — cropped exactly
to your screen, with an optional info bar showing satellite/sector/product and capture
time.

![Sample GOES-18 GEOCOLOR wallpaper](sample_wallpaper.jpg)

## Contents

[Requirements](#requirements) · [Setup](#setup) · [Configuration](#configuration) ·
[Multi-source combos](#multi-source-combos) ·
[Georeferenced overlays](#georeferenced-overlays) ·
[Power/network-aware fallbacks](#powernetwork-aware-fallbacks) ·
[Cross-platform](#cross-platform) · [Freshness sync](#freshness-sync) ·
[Running periodically](#running-periodically) ·
[Source image caveats](#source-image-caveats) ·
[Notes and known limitations](#notes-and-known-limitations) · [Tests](#tests) ·
[Contributing](#contributing) · [Changelog](#changelog) · [License](#license)

## Requirements

* Windows, Python 3.11+ (see "Cross-platform" below — only Windows has a working
  backend today)
* [uv](https://docs.astral.sh/uv/) (recommended) or a manual venv + `pip install -e .`

## Setup

```powershell
uv sync
```

This creates `.venv/` and installs the dependencies — `requests`, `Pillow`,
`pyproj`/`numpy` for georeferenced overlays, plus Windows-only packages (`comtypes`
for the per-monitor wallpaper API, `winrt-Windows.System.Power`/`winrt-Windows.
Networking.Connectivity` for battery/network-cost detection) that `uv sync` skips
automatically on other platforms via `pyproject.toml`'s environment markers. Do a
one-off test run with:

```powershell
uv run python goes_wallpaper.py
```

That downloads the latest configured image, crops it to your screen, and sets it as
your wallpaper immediately — check `%LOCALAPPDATA%\GOES-Wallpaper\` for the saved
`wallpaper.jpg`, `wallpaper.json` (metadata), and `log.txt`.

### Alternative: install as a package

Instead of running from a source checkout, `uv build` (or `pip install .`) produces a
proper wheel with two entry points — `goes-wallpaper` (console) and `goes-wallpaperw`
(windowed, no console popup — for a shortcut/Task Scheduler-style launch):

```powershell
uv build
uv tool install (Get-Item dist\*.whl)
goes-wallpaper --config path\to\config.toml
```

An installed copy has no `config.toml` next to it (unlike a source checkout, where
one lives beside the script) — pass `--config` explicitly (see
[config.example.toml](config.example.toml) for a minimal starting point), or
everything falls back to the built-in defaults.

#### Installing a pre-built release

Skip the build step entirely by grabbing the wheel from the
[latest GitHub release](https://github.com/John-Schreiber/GOES-Wallpaper/releases/latest)
instead of cloning the repo:

```powershell
uv tool install https://github.com/John-Schreiber/GOES-Wallpaper/releases/download/v2.0.0/goes_wallpaper-2.0.0-py3-none-any.whl
goes-wallpaper --config path\to\config.toml
```

(Or download the `.whl` manually and point `uv tool install`/`pip install` at the local
file.) Same `--config` caveat as above applies — a release install has no `config.toml`
bundled next to it either.

## Configuration

Behavior is driven by [config.toml](config.toml), which the script reads by default
from next to itself (override with `--config path\to\other.toml`). Every field has an
inline comment; the highlights:

* **Source image** — `satellite` (`GOES19` east / `GOES18` west), `sector` (`CONUS`,
  `FD` full disk, `M1`/`M2` mesoscale), `product` (e.g. `GEOCOLOR`), `resolution`. NOAA
  serves discrete sizes per sector, not an arbitrary resize (verified for CONUS:
  625x375 up to 10000x6000 native; Full Disk differs: 1808x1808 up to 10848x10848).
  Default is `5000x3000` so a full-frame crop already covers a 4K monitor without
  upsampling blur — bump higher if you crop aggressively via `source_crop_*`/combos.
* **Screen handling** — `crop_to_screen` does a Lanczos resize + center-crop ("cover"
  style) so the image exactly fills your screen without Windows' own lower-quality
  scaling; `crop_anchor` biases where the crop is taken from; `span_all_monitors` crops
  to the full virtual desktop instead of just the primary monitor (pair with
  `wallpaper_style = "span"`). `source_crop_left/top/right/bottom` (fractions of the
  source frame, default the full frame) crop *before* that resize, to deliberately
  frame a region of interest — or to cut off NOAA's logo watermark, which sits in the
  bottom-left corner of every frame this CDN serves. See "Source image caveats" below.
* **Freshness sync** — learns *when within each interval* NOAA actually publishes a
  new frame and schedules around that, instead of guessing at the raw clock boundary.
  See "Freshness sync" below.
* **Info block / EXIF** — an overlay bar with satellite/sector/product and localized
  capture time; the same details are also baked into the JPEG's EXIF tags.
  `avoid_taskbar` (default on) queries the live taskbar height and nudges the bar
  above it, since the wallpaper renders full-screen behind the taskbar and a bar
  drawn at the very bottom edge would otherwise be clipped.
* Any field can also be overridden via CLI flag, e.g. `--sector FD --no-info-block`.
  Run `uv run python goes_wallpaper.py --help` for the full list.
* **Multi-source combos** — `combo_mode` (`"single"` default / `"rotate"` /
  `"per_monitor"`) plus a list of named `[[combos]]`, each optionally overriding
  satellite/sector/product/resolution and carrying its own crop box. See "Multi-source
  combos" below.
* **Georeferenced overlays** — `overlay_graticule` (lat/lon grid) and `overlay_cities`
  (labeled markers) drawn accurately onto the image, CONUS only. See "Georeferenced
  overlays" below.

## Multi-source combos

Beyond the single top-level source, `config.toml` can define named combos and a
`combo_mode` for how to use them:

* **`"rotate"`** cycles through the combo list one per cycle — each `--loop` cycle (or
  each Task Scheduler run) shows a different source/crop, remembering where it left
  off in `state.json`. Good for variety on a single monitor: e.g. alternate between
  GEOCOLOR and Clean IR, or between GOES East and West.
* **`"per_monitor"`** assigns one combo per physical monitor via `monitor` (0-based),
  and applies each independently through the platform backend's per-monitor wallpaper
  support (Windows: the `IDesktopWallpaper` COM interface) — genuinely different images
  on different screens, each cropped to that monitor's own resolution rather than one
  image spanning/tiling across all of them. Every combo must set `monitor` in this
  mode; a monitor with no assigned combo is left untouched. Each assigned combo
  triggers its own download, so total cycle time scales with how many distinct combos
  you assign.

Any combo field left unset falls back to the top-level `satellite`/`sector`/`product`/
`resolution`; the crop fields (`crop_left/top/right/bottom`) always apply and default
to no crop. See the commented examples in [config.toml](config.toml).

Note on monitor numbering: the `monitor` index refers to the enumeration order the
platform backend reports (Windows: `IDesktopWallpaper`'s order), which isn't
guaranteed to match the numbers shown in Windows' Display Settings. If wallpapers
land on the wrong screen, swap the indices.

## Georeferenced overlays

`overlay_graticule` (a lat/lon grid) and `overlay_cities` (labeled markers at exact
coordinates) can be drawn accurately onto the image — accurately meaning genuinely
georeferenced, not just eyeballed: `lonlat_to_pixels()` projects real lon/lat into the
image's actual GEOS satellite projection using `pyproj`, with the projection extent
for each satellite's CONUS sector derived from a real ABI L1b radiance file (loaded
with `satpy` during development, not a runtime dependency) and validated against 10
known city landmarks — median error well under a pixel at 2500×1500.

**CONUS only.** The calibration constants are specific to each satellite's CONUS
sector extent; Full Disk wasn't calibrated, and Mesoscale sectors move (NOAA
repositions them), so their extent can't be hardcoded the same way. Enabling an
overlay on a non-CONUS sector logs a warning and skips drawing rather than rendering
something misplaced.

This adds content on top — it doesn't and can't remove NOAA's own baked-in state
lines/logo (see "Source image caveats" below for why). Marker/line sizes are tuned for
a ~2000px-wide frame and scale up automatically at higher `resolution` settings.

## Power/network-aware fallbacks

`skip_on_battery` (skip the whole cycle if running on battery power) and
`metered_resolution` (fetch a smaller size when the network connection is
cost-metered, e.g. cellular/tethered) are both off by default. Detection goes through
the platform backend (see "Cross-platform" below) and degrades to "unknown"
gracefully on hardware/platforms that can't detect it — unknown is always treated as
"not constrained," so enabling these never risks skipping or downgrading on a guess.

## Cross-platform

OS-specific operations (applying the wallpaper, screen/monitor detection, taskbar/dock
avoidance, battery/network-cost detection) live behind `platform_base.WallpaperPlatform`,
implemented today only by `platform_windows.WindowsPlatform`. `goes_wallpaper.py`
itself — the fetch/crop/overlay/combo/scheduling logic — has no Windows-specific code
left in it.

To port to a new OS: implement every method on `WallpaperPlatform` in a new
`platform_<name>.py` (see `platform_windows.py`'s docstring and method-by-method
comments for what each one needs to do and how the Windows implementation validated
each against real hardware), then add a branch for it in `platform_base.get_platform()`.
Linux and macOS backends are wanted — no specific desktop environment is prioritized
over another, so pick whichever you actually use (KDE, GNOME, and macOS all fit the
same interface). Contributions beyond new platform backends are welcome too. Note
that `pyproject.toml` marks the Windows-only dependencies (`comtypes`, the `winrt-*`
packages) with `sys_platform == 'win32'` markers, so a new backend's dependencies
should get the equivalent marker for its own platform.

## Freshness sync

NOAA doesn't publish a new frame right on the clock boundary — there's a
processing/CDN lag after each scan (observed ~40-55s past the boundary on
CONUS/GEOCOLOR, but it varies by satellite/product). Three settings, layered:

* **`sync_to_capture_time`** (on by default) learns the lag from each frame's actual
  capture time and schedules `--loop`'s next wake-up shortly after, instead of
  guessing at the raw boundary and often catching the previous frame. It has nothing
  to learn from on the very first cycle — that one falls back to plain clock-boundary
  alignment, same as if this were off. The learned offset is persisted per-source in
  `state.json` (next to `wallpaper.json`), so it survives restarts.
* **`wait_for_fresh_capture`** (on by default) is the in-cycle backstop: if a download
  comes back with the same capture time as last cycle (the new frame just hasn't
  posted yet), it retries a few times before giving up rather than applying stale
  content.
* **`--wait-for-sync`** (off by default) is for single-shot/Task Scheduler use — see
  "Running periodically" below.

## Running periodically

Two options — pick one, not both:

### Option A: built-in `--loop` mode

```powershell
uv run python goes_wallpaper.py --loop
```

Runs indefinitely, sleeping until the next scheduled cycle (`interval_minutes` in
`config.toml`, default 5). This is the simplest option for a machine that's normally
on and logged in — start it once (e.g. from a shortcut in your Startup folder) and
leave it running.

### Option B: Windows Task Scheduler

Closer to how the original version of this script was run, and works well if you'd
rather Task Scheduler own the retry/restart semantics:

* **Trigger**: one-time trigger starting whenever you set it up, then "repeat every X
  minutes/hours indefinitely." NOAA publishes a new CONUS image every 5 minutes.
* **Action**: start a program —
  * Program: `C:\path\to\GOES-Wallpaper-fork\.venv\Scripts\pythonw.exe`
  * Arguments: `goes_wallpaper.py`
  * Start in: `C:\path\to\GOES-Wallpaper-fork`

  Use the venv's `pythonw.exe`, not a bare system one — it's the interpreter `uv sync`
  actually installed the dependencies into. `pythonw.exe` (vs `python.exe`) runs
  without popping up a console window, regardless of the target script's extension.
  If you installed the package instead (see "Alternative: install as a package"
  above), point Program at `goes-wallpaperw.exe` directly instead and leave
  Arguments/Start in blank.
* **Condition**: start only if a network connection is available.
* **Settings**: run task as soon as possible after a missed scheduled start; don't
  start a new instance if one's already running; **run only when a user is logged
  on** — see the note below on why this matters.

Add `--wait-for-sync` to the arguments if you'd rather the script sleep once until
shortly after the next frame's learned publish time, instead of fetching immediately
and relying on `wait_for_fresh_capture`'s poll-and-retry loop — no-op until a phase
has been learned from a prior run, and capped by `wait_for_sync_max_seconds` so it
can't hang the task for most of a cycle if your trigger interval doesn't match
`interval_minutes`.

## Source image caveats

NOAA STAR's CDN bakes some things into the image pixels themselves, which this script
can't strip out — checked directly against GEOCOLOR, Band 02 (visible), and Band 13
(Clean IR) for the CONUS sector:

* **State/country border lines are present on every product**, not just GEOCOLOR.
  They're drawn across the whole frame, not confined to an edge, so no crop setting
  can remove them — only NOAA not rendering them would.
* **"Fake" city lights are GEOCOLOR-specific.** GEOCOLOR blends a static VIIRS
  nighttime-lights composite into the night side so cities are visible in the dark —
  it's not real-time light data. Raw bands like `13` (Clean Longwave IR, works day and
  night) don't do this compositing at all, at the cost of losing GEOCOLOR's true-color
  daytime look in exchange for grayscale IR.
* **NOAA's logo watermark** sits in the bottom-left corner of every frame. Use
  `source_crop_left` (e.g. `0.10`) to trim it off before the screen-fit crop — see the
  config comments for the full `source_crop_*` set.

## Notes and known limitations

* **Screen size detection normally needs an interactive session**, and falls back to
  `1024x768` without one (e.g. a scheduled task running "whether user is logged on or
  not," or a locked/disconnected RDP session). `wmi_screen_size_fallback` (default on)
  automatically recovers the real resolution via WMI in that case — it reads the video
  driver's current mode directly instead of going through the window station, so it
  isn't affected by the same limitation. If WMI also can't find a resolution, or you'd
  rather not rely on it, set `screen_width`/`screen_height` explicitly in
  `config.toml`; the script logs a warning if it ends up on the `1024x768` fallback.
* **Multi-monitor via `span_all_monitors`** requires Windows 8+ for the `"span"`
  wallpaper style to actually stretch one image across all displays; see "Freshness
  sync" above for the scheduling behavior and "Multi-source combos" above for
  genuinely different images per monitor instead of one spanned image.

## Tests

```powershell
uv run pytest
```

Covers config loading/validation, source resolution, crop math, the
freshness-sync/wait-for-sync scheduling math, and the CONUS georeferencing (regression
tests against real city landmarks — see `tests/test_geolocation.py`'s docstring for
what that test does and doesn't prove). No real network access or Windows APIs
required — platform-specific behavior is tested through a fake `WallpaperPlatform`
stub (`tests/test_power_network_fallback.py`), the same pattern used to develop the
power/network-aware fallbacks in the first place.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — a Linux or macOS platform backend is the
contribution most likely to be useful right now, but other changes are welcome too.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

GNU General Public License v3.0-or-later — see [LICENSE](LICENSE). This project
began as a clone of an Apache-2.0-licensed original and has since been substantially
rewritten; see [ATTRIBUTION.md](ATTRIBUTION.md) for the full origin/credits and the
preserved original license notice.
