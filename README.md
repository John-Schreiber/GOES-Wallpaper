# GOES Desktop Wallpaper Updater

A Python script that downloads the most recent image from a GOES weather satellite
(NOAA STAR's public CDN) and sets it as the desktop wallpaper — on Windows, KDE
Plasma (Linux), or macOS — cropped exactly to your screen, with an optional info bar
showing satellite/sector/product and capture time.

![Sample GOES-18 GEOCOLOR wallpaper](sample_wallpaper.jpg)

## Contents

[Requirements](#requirements) · [Setup](#setup) · [Configuration](#configuration) ·
[Multi-source combos](#multi-source-combos) ·
[Georeferenced overlays](#georeferenced-overlays) (full schema: [OVERLAYS.md](OVERLAYS.md)) ·
[Output projection](#output-projection) ·
[Custom raw-data source (satpy_raw)](#custom-raw-data-source-satpy_raw) ·
[Power/network-aware fallbacks](#powernetwork-aware-fallbacks) ·
[Cross-platform](#cross-platform) · [Freshness sync](#freshness-sync) ·
[Running periodically](#running-periodically) (full guide: [RUNNING.md](RUNNING.md)) ·
[Source image caveats](#source-image-caveats) ·
[Notes and known limitations](#notes-and-known-limitations) · [Tests](#tests) ·
[Contributing](#contributing) · [Changelog](#changelog) · [License](#license)

## Requirements

* Python 3.11+, and one of:
  * **Windows**, or
  * **Linux running KDE Plasma** (5.24+ recommended, for `plasma-apply-wallpaperimage`;
    older Plasma 5 falls back to D-Bus scripting — see "Cross-platform" below). Other
    Linux desktop environments (GNOME, etc.) aren't implemented yet.
  * **macOS**, via `pyobjc`'s `NSWorkspace`/`NSScreen` bindings (see "Cross-platform"
    below) — the default single-screen path has been confirmed on a real MacBook;
    multi-monitor and battery-state detection are still only unit-tested, not
    live-verified (see "Cross-platform" below).
* [uv](https://docs.astral.sh/uv/) (recommended) or a manual venv + `pip install -e .`

## Setup

```powershell
uv sync
```

(Linux/macOS shells: same command, just without the `.ps1`-flavored examples below —
use `uv run python goes_wallpaper.py` etc. exactly as shown, and `~/.local/share/`
paths instead of `%LOCALAPPDATA%` where noted.)

This creates `.venv/` and installs the dependencies — `requests`, `Pillow`,
`pyproj`/`numpy` for georeferenced overlays, plus OS-specific packages that `uv sync`
only installs on the matching platform via `pyproject.toml`'s environment markers:
`comtypes`/`winrt-Windows.System.Power`/`winrt-Windows.Networking.Connectivity` on
Windows (per-monitor wallpaper API, battery/network-cost detection); on Linux, the KDE
backend instead shells out to `qdbus6`/`qdbus`, `plasma-apply-wallpaperimage`,
`upower`, and `nmcli` — all typically already present in a Plasma desktop install, not
Python packages. Do a one-off test run with:

```powershell
uv run python goes_wallpaper.py
```

That downloads the latest configured image, crops it to your screen, and sets it as
your wallpaper immediately — check `%LOCALAPPDATA%\GOES-Wallpaper\` (Windows) or
`~/AppData/Local/GOES-Wallpaper/` (Linux — same relative default path, just resolved
under `$HOME`) for the saved `wallpaper.jpg`, `wallpaper.json` (metadata), and
`log.txt`.

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
$whl = (Invoke-RestMethod https://api.github.com/repos/John-Schreiber/GOES-Wallpaper/releases/latest).assets.browser_download_url | Where-Object { $_ -like "*.whl" }
uv tool install $whl
goes-wallpaper --config path\to\config.toml
```

(Resolves whatever the current latest release's wheel is via the GitHub API, so this
command doesn't need editing every release — no version number hardcoded.)

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
* **`source_kind`** — `"cdn_jpg"` (default, the above) or `"satpy_raw"` (composite our
  own image from raw satellite data instead, no NOAA annotations — heavier, opt-in,
  needs an extra install). See "Custom raw-data source (satpy_raw)" below.
* **Screen handling** — `crop_to_screen` does a Lanczos resize + center-crop ("cover"
  style) so the image exactly fills your screen without Windows' own lower-quality
  scaling; `crop_anchor` biases where the crop is taken from; `span_all_monitors` crops
  to the full virtual desktop instead of just the primary monitor (pair with
  `wallpaper_style = "span"`). `source_crop_left/top/right/bottom` (fractions of the
  source frame, default the full frame) crop *before* that resize, to deliberately
  frame a region of interest — or to cut off NOAA's logo watermark, which sits in the
  bottom-left corner of every frame this CDN serves. See "Source image caveats" below.
  `source_crop_min_lon/min_lat/max_lon/max_lat` frame the same region-of-interest crop
  by a lon/lat bounding box instead, for whichever satellite/sector actually gets
  fetched — see [OVERLAYS.md](OVERLAYS.md) for the calibration this relies on.
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
* **Georeferenced overlays** — a lat/lon grid, labeled city markers, GeoJSON files,
  and a live shell-command GeoJSON source, drawn accurately onto the image for CONUS
  and Full Disk sectors — configured separately, in `overlays.toml`, not here. See
  [OVERLAYS.md](OVERLAYS.md).
* **`output_projection`** — reproject the rendered frame into `platecarree`/
  `lambertconformal` (framed by `source_crop_min_lon`/etc.) or `orthographic`/
  `lambertazimuthal` (a globe view) instead of the satellite's native GEOS view. See
  "Output projection" below and [PROJECTIONS.md](PROJECTIONS.md) for example renders.

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
to no crop. `crop_min_lon/min_lat/max_lon/max_lat` (the lon/lat crop-box alternative —
see "Georeferenced overlays" below) behave like the source-selection fields instead:
unset on a combo falls back to the top-level `source_crop_min_lon`/etc. rather than
always applying. See the commented examples in [config.toml](config.toml).

Note on monitor numbering: the `monitor` index refers to the enumeration order the
platform backend reports (Windows: `IDesktopWallpaper`'s order), which isn't
guaranteed to match the numbers shown in Windows' Display Settings. If wallpapers
land on the wrong screen, swap the indices.

## Georeferenced overlays

A lat/lon grid, labeled city markers, static GeoJSON files, and a live shell-command
GeoJSON source can all be drawn accurately onto the image — accurately meaning
genuinely georeferenced, not just eyeballed. Configured in a separate file,
`overlays.toml`, not `config.toml`. See [OVERLAYS.md](OVERLAYS.md) for the full
schema, styling rules, and the CONUS/Full Disk-only calibration caveat (any sector
with `source_kind = "satpy_raw"`, below).

## Output projection

`output_projection` reprojects the rendered frame into a different map projection
instead of the satellite's native GEOS view. `"native"` (default) means no
reprojection; otherwise:

* **Bounds-framed** (`source_crop_min_lon/min_lat/max_lon/max_lat` above, required in
  these modes — those bounds become the reprojected output's extent):
  * `"platecarree"` — equirectangular.
  * `"lambertconformal"` — conformal conic, the standard choice for a mid-latitude
    regional map (what NWS/NOAA's own CONUS maps use) — negligible distortion for a
    CONUS-sized box, unlike `platecarree`/Mercator. Standard parallels default to 1/6
    and 5/6 up the box's latitude range; override with
    `output_projection_lcc_lat1`/`_lcc_lat2` for specific ones.
* **Center-framed** (`output_projection_center_lon`/`output_projection_center_lat`,
  defaulting to the resolved source's own satellite sub-point and the equator):
  * `"orthographic"` — a globe view as seen from space; pixels outside the visible
    hemisphere render black — that's "space," not a bug.
  * `"lambertazimuthal"` — equal-area azimuthal; shows nearly the whole globe, not
    just the visible hemisphere, without Mercator's polar blowup, at the cost of
    shape distortion far from center.

See [PROJECTIONS.md](PROJECTIONS.md) for example renders of each.

Implemented with nearest-neighbor resampling via `pyproj`/`numpy` only — no
`pyresample`/`satpy` dependency — so it works for both the default `cdn_jpg` source
(CONUS/Full Disk, using the same hand-calibrated extents overlays use — see
[OVERLAYS.md](OVERLAYS.md)) and `source_kind = "satpy_raw"` (any sector, using its
real per-frame georeferencing).
Reprojection replaces rather than stacks with the region-of-interest crop — its own
bounds/framing already define what survives. Falls back to the native projection,
logged, if there's no calibration for the resolved satellite/sector. Not
combo-overridable — every combo shares one `output_projection` if set.

Caveat: `cdn_jpg`'s baked-in NOAA captions/state borders warp along with everything
else near the projection's edges, since they can't be distinguished from the rest of
the image (see "Source image caveats" below) — this is most noticeable in
`"orthographic"`/`"lambertazimuthal"` near the limb of the globe.

Quality caveat: reprojection is nearest-neighbor only (no smoothing/anti-aliasing),
so the valid-data/black boundary in `"orthographic"`/`"lambertazimuthal"` renders
visibly jagged rather than a clean curve — see the gallery in
[PROJECTIONS.md](PROJECTIONS.md). It also runs *after* overlays (see
[OVERLAYS.md](OVERLAYS.md)) are drawn (not before), so graticule lines, city
markers/labels, and GeoJSON overlays get warped
along with the base image instead of being redrawn cleanly in the destination
projection — thin lines can break into dashed/patchy pixels and text labels can
shear, worst near the projection's edges (same region the caveat above already
flags). `"lambertconformal"`/`"platecarree"`, being close to the source projection
over a CONUS-sized box, show this least. See `NEXT_STEPS.md` for the tracked
follow-up.

## Custom raw-data source (satpy_raw)

`source_kind = "satpy_raw"` fetches raw ABI L1b radiance bands directly from the
public `noaa-goes16`/`noaa-goes18`/`noaa-goes19` S3 buckets (anonymous access, no
credentials needed) and composites a GeoColor image locally with
[satpy](https://satpy.readthedocs.io/), instead of fetching NOAA STAR's
pre-rendered JPG. Unlike the default `cdn_jpg` source, this has **no baked-in state
lines, logo, or fake city lights** — there's nothing to remove because we're
building the image ourselves — and it exposes the real projection/area info
directly, so [georeferenced overlays](#georeferenced-overlays) work accurately on
Full Disk and Mesoscale sectors too, not just CONUS. See
[CUSTOM_IMAGERY_PLAN.md](CUSTOM_IMAGERY_PLAN.md) for the full design rationale.

Install the extra it needs (not part of the default install — these are heavy
geospatial libraries most users don't need):

```powershell
uv sync --extra satpy-raw
# or: pip install goes-wallpaper[satpy-raw]
```

Then set, top-level or per-combo:

```toml
source_kind = "satpy_raw"
satellite = "GOES18"
sector = "CONUS"   # CONUS, FD, M1, or M2
```

`product` and `resolution` are ignored for this source_kind — satpy always builds
a GeoColor-style composite from a fixed band set (C01/C02/C03/C13); there's no
NOAA product code or JPG size tier equivalent. `metered_resolution` is similarly a
no-op here (no smaller-tier download exists for raw bands).

**Status**: first cut, opt-in alongside the default `cdn_jpg` source (not a
replacement) — no automatic fallback to `cdn_jpg` if a raw fetch fails, and no
cross-cycle *reuse* of downloaded band files (each cycle fetches the latest scan
fresh into `<data_dir>/satpy_raw_cache`; the previous cycle's files are deleted
first, so this doesn't accumulate — see the disk-leak fix in
[NEXT_STEPS.md](NEXT_STEPS.md)). Verified end to end against live GOES-18/GOES-19
data for both CONUS and Full Disk: real S3 listing/download, real compositing
(including the day/night blend below, confirmed against a real terminator), and the
full crop/info-block/EXIF pipeline producing a correct final image with no NOAA
annotations.

![Sample GOES-19 Full Disk satpy_raw render, showing the day/night blend at a real terminator](sample_wallpaper_satpy_night.jpg)

**Night side**: not GEOCOLOR-style synthetic city lights, by design (those come
from a static VIIRS composite, not real-time data — see CUSTOM_IMAGERY_PLAN.md).
Instead, `source_satpy.py` builds its own day/night blend: true-color by day, a
muted navy-to-pale-lavender color mapped from Band 13 (clean IR window) brightness
temperature by night, blended at the real per-pixel solar terminator — a
deliberately photographic/moonlit feel rather than a false-color IR product or
flat darkness. (This also sidesteps satpy's stock `geo_color` composite, whose
night layer depends on a NASA-hosted Black Marble file that currently 404s — an
external outage outside our control, not something this path relies on.) Real
VIIRS Day/Night Band city lights would be a genuine future upgrade over this — see
CUSTOM_IMAGERY_PLAN.md's backlog.

**Bandwidth and compute cost — read before enabling on a `--loop` interval.**
This is a fundamentally heavier source than `cdn_jpg`'s single small JPG fetch
(~2-9MB observed for CONUS in this session): every cycle downloads four raw band
files and composites them locally, with no cross-cycle caching in v1. A live
GOES-18 CONUS fetch measured **~98MB** for the four bands, and Full Disk is
considerably more (Band 2 alone was ~405MB natively in one live fetch). Every
fetch logs the actual downloaded byte count (`source_satpy.fetch_composite`'s
"Downloaded %d bytes" line) — watch it before committing to a schedule. Compositing
itself is done at a downsampled resolution (not each band's full native resolution
— see `_COMPOSITE_TARGET_WIDTH_PX` in `source_satpy.py`) to keep compute
reasonable, which brought a single composite down to roughly 22s for CONUS and 45s
for Full Disk in testing — but that only helps compute, not the download, which
still has to pull each band at full native resolution first. Think carefully
before enabling `satpy_raw` at a tight `interval_minutes`, especially for Full
Disk, and especially on a metered/limited connection — `metered_resolution` can't
help here (see above). Test with `--render-to` (see "Tests" below) before
committing to a `--loop` schedule.

Georeferenced overlays (a lat/lon grid, city markers, GeoJSON files, a live
shell-command source) work on any sector with this source_kind, not just CONUS/Full
Disk, via its real per-frame georeferencing — see [OVERLAYS.md](OVERLAYS.md) for the
full schema and styling rules.

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
implemented by `platform_windows.WindowsPlatform`, `platform_linux_kde.KDEPlatform`,
and `platform_macos.MacOSPlatform`. `goes_wallpaper.py` itself — the fetch/crop/
overlay/combo/scheduling logic — has no OS-specific code in it; `platform_base.
get_platform()` picks a backend automatically from `sys.platform` (Windows, macOS) or
`XDG_CURRENT_DESKTOP`/`XDG_SESSION_DESKTOP` containing `"kde"` (Linux) and raises
`NotImplementedError` for any other Linux desktop environment.

Set `platform` in config.toml (`"auto"` default, or explicit `"windows"`/`"kde"`/
`"macos"`) to short-circuit that detection — e.g. a Plasma session where
`XDG_CURRENT_DESKTOP` isn't set the way Plasma normally sets it, or for testing a
specific backend. No CLI flag for this yet; config.toml only.

### KDE Plasma backend

`platform_linux_kde.KDEPlatform` talks to Plasma's own D-Bus scripting interface
(`qdbus6`/`qdbus … org.kde.PlasmaShell.evaluateScript`, the same JS API Plasma's own
desktop shell scripting uses) for screen geometry, panel/taskbar height, and
per-monitor wallpaper assignment — not X11-only tools like `xrandr`, since Plasma also
needs to work under Wayland. Applying a single whole-desktop wallpaper prefers the
`plasma-apply-wallpaperimage` CLI (shipped since Plasma 5.24) when present, falling
back to the same D-Bus scripting otherwise. Battery and metered-network detection
shell out to `upower`/`nmcli` directly (the de facto standard on Linux desktops
generally, not Plasma-specific).

Known limitations:

* **No "span" equivalent.** KDE has no native "one image spanning all monitors"
  wallpaper plugin the way Windows' `IDesktopWallpaper` has `DWPOS_SPAN` — each
  screen's containment crops its own wallpaper independently. `wallpaper_style =
  "span"` degrades to `"fill"` (cover-crop per screen) with a logged warning rather
  than producing a misaligned image.
* **Requires a live desktop session.** Every KDE operation needs a running
  `plasmashell` with a reachable session D-Bus (`DBUS_SESSION_BUS_ADDRESS` pointing at
  the logged-in user's bus) — a bare cron job or a systemd *system* (not `--user`)
  service won't have that. See "Running periodically" below for the systemd `--user`
  timer setup this requires.
* **Verification status**: the default single-screen apply path (`get_screen_size` +
  `apply_wallpaper`) has been confirmed against a real Plasma session — the desktop's
  actual `org.kde.image` wallpaper config was checked directly via `qdbus6 …
  evaluateScript` and matched the freshly-rendered file after a run. `per_monitor`
  combo mode, real multi-monitor geometry, panel-height detection against an actual
  panel, and `upower`/`nmcli` output parsing are still only covered by the unit
  tests' mocked subprocess output, not live multi-monitor/battery/metered-network
  hardware — see `NEXT_STEPS.md` item 11 for specifics.

### macOS backend

`platform_macos.MacOSPlatform` uses `pyobjc`'s `AppKit`/`Foundation` bridge to call
`NSWorkspace`/`NSScreen` directly — the OS's actual supported API for setting the
desktop image and reading screen geometry, not a shell-out hack. Wallpaper-scaling
options are mapped per style using the same recipe as the community `desktoppr` tool
(https://github.com/scriptingosx/desktoppr). Battery state shells out to `pmset -g
batt` (mirroring the KDE backend's `upower`/`nmcli` approach); there's no reliable
API for per-network metered/"Low Data Mode" status, so `is_network_metered()` always
returns `None` on macOS.

Known limitations:

* **No "tile" or "span" equivalent.** `NSWorkspace`'s desktop-image options have no
  tiling mode (removed from System Settings' own UI in recent macOS) and no
  spanning mode (inherently per-`NSScreen`) — both degrade to `"fill"` with a logged
  warning, the same pattern the KDE backend uses for `"span"`.
* **Verification status: single-screen path confirmed, multi-monitor still open.**
  The default single-screen apply path (`get_screen_size` + `apply_wallpaper`) has
  been confirmed on a real MacBook with a single (built-in) display, the same
  verification bar as the Windows and KDE backends' default paths. `list_monitors`/
  `apply_wallpaper_per_monitor` against real multi-monitor geometry and
  `get_power_state`'s `pmset -g batt` parsing on battery are still only covered by
  the unit tests' mocked output, not live multi-monitor/battery hardware — see
  `NEXT_STEPS.md` item 22 for specifics.

### Adding another OS/desktop environment

To port to a new OS or Linux desktop environment: implement every method on
`WallpaperPlatform` in a new `platform_<name>.py` (see `platform_windows.py`'s and
`platform_linux_kde.py`'s docstrings and method-by-method comments for what each one
needs to do and how each existing implementation validated against real hardware —
`platform_macos.py` is a third reference implementation worth reading too, mainly
for how it handles Cocoa's bottom-up screen-coordinate system and NSWorkspace's
per-screen API shape; its default single-screen path is now confirmed live like the
other two, though its multi-monitor/battery paths are still unit-test-only, see
"macOS backend" above), then add a branch for it in `platform_base.
get_platform()`. A backend for any other OS or desktop environment (GNOME, Cinnamon,
XFCE, etc.) is welcome — none prioritized over another, pick whichever you actually
use. Contributions beyond new platform backends
are welcome too. Note that `pyproject.toml` marks platform-specific Python
*package* dependencies (`comtypes`, the `winrt-*` packages, `pyobjc-framework-Cocoa`)
with `sys_platform == 'win32'`/`'darwin'` markers; the KDE backend instead depends on
external binaries
(`qdbus6`/`qdbus`, `plasma-apply-wallpaperimage`, `upower`, `nmcli`) that aren't
`pyproject.toml` dependencies at all — it degrades gracefully (logs a warning, returns
an "unknown"/conservative default) if one is missing rather than failing to import.

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
  [RUNNING.md](RUNNING.md).

## Running periodically

Three options — the built-in `--loop` mode, Windows Task Scheduler, or a Linux
systemd `--user` timer — with setup instructions for each and why you'd pick one
over another. See [RUNNING.md](RUNNING.md).

## Source image caveats

These apply to the default `source_kind = "cdn_jpg"`, which fetches NOAA STAR's
already-rendered JPG. `source_kind = "satpy_raw"` (see above) doesn't have any of
these, since it composites the image from raw bands itself instead.

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
power/network-aware fallbacks in the first place. `tests/test_source_satpy.py` covers
`source_kind = "satpy_raw"`'s pure band/scan-selection logic without needing the
`satpy-raw` extra installed; real S3/satpy exercise is manual-only (see above).

To manually inspect a real render (either source_kind) without touching your actual
desktop wallpaper, use `--render-to`:

```powershell
uv run python goes_wallpaper.py --render-to test_render.jpg
```

This runs one full fetch/crop/overlay/info-block cycle and saves the result to the
given path, skipping `platform.apply_wallpaper(...)` entirely — useful for checking a
new `source_kind`, overlay, or crop setting looks right before enabling it for real.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — Windows and KDE Plasma both have working
backends now; a backend for any other OS/desktop environment is a welcome
contribution (none prioritized over another), and other changes are welcome too.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

GNU General Public License v3.0-or-later — see [LICENSE](LICENSE). This project
began as a clone of an Apache-2.0-licensed original and has since been substantially
rewritten; see [ATTRIBUTION.md](ATTRIBUTION.md) for the full origin/credits and the
preserved original license notice.
