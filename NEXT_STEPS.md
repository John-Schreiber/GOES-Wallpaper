# Modernization status & next steps

Working notes for continuing `goes_wallpaper` development. This repo is a real
GitHub fork of https://github.com/pjlhjr/GOES-Wallpaper, substantially rewritten
since (see [CHANGELOG.md](CHANGELOG.md) for what's actually shipped — this file is
for forward-looking notes and open questions, not a second copy of the changelog).

For removing NOAA's baked-in state lines/city lights and adding custom overlays from
raw satellite data — a separate, bigger initiative than anything below — see
[CUSTOM_IMAGERY_PLAN.md](CUSTOM_IMAGERY_PLAN.md).

## Verification notes worth knowing

A few non-obvious things learned while building and testing this, not really
"gaps" but easy to waste time rediscovering:

- **GOES-19 (the default `satellite`) has been offline for extended periods** during
  development — its CDN endpoint serves an unchanging frame (304s on every poll) and
  its S3 raw-data bucket goes hours without a new file. GOES-18 has been reliably
  live throughout; use `--satellite GOES18` for testing if GOES-19 looks stuck.
- **`combo_mode = "per_monitor"` has been verified against real multi-monitor
  hardware**, not just code review — worth knowing since it's easy to test this only
  via reasoning about `GetSystemMetrics`/`IDesktopWallpaper` calls otherwise.
- **`IDesktopWallpaper` can report stale monitor device paths** for a display that's
  no longer connected (errors on `GetMonitorRECT`). `_list_active_monitors()` already
  skips entries that error, so `combo.monitor` indices refer to the *active*
  enumeration order, not the raw `GetMonitorDevicePathAt` index — worth remembering
  if a setup's monitor numbering looks off.
- **The georeferencing calibration is hardcoded, not self-updating.** The GEOS extent
  constants in `_GEOS_AREA_CONUS` (`goes_wallpaper.py`) were derived once from real
  ABI L1b files. Geostationary satellites drift slightly and undergo station-keeping
  maneuvers, so if overlays start looking subtly off, re-derive by loading a fresh
  raw CONUS file with `satpy` and reading `scn[...].attrs['area']` rather than
  assuming the constants are permanent.

## Known gaps / follow-up

1. **`--loop` mode has only been exercised for one real cycle** at a time — never a
   long supervised run spanning several real sleep/wake cycles, to confirm the
   learned-phase scheduling converges over time and repeated cycles don't leak file
   handles/sessions.
2. **`trim_source_caption_frac = 0.02`** was measured from one CONUS/GEOCOLOR frame.
   It's a fixed fraction of height, which should scale reasonably with resolution,
   but hasn't been checked against Full Disk or Mesoscale sectors, which may render
   NOAA's caption bar at a different relative size.
3. **`span_all_monitors` (one image spanned across all monitors) is unverified
   visually** — unlike `combo_mode = "per_monitor"` (verified live against real
   hardware), this path was only checked by reading the `GetSystemMetrics(78/79)`
   call. `avoid_taskbar` has a related caveat there: it assumes the taskbar sits at
   the bottom of the rendered image, which may not hold for a spanned virtual-desktop
   image (taskbar on a monitor other than the bottom-most one, or a per-monitor
   taskbar on Windows 11) the way it does for `per_monitor` mode (which measures each
   monitor's real taskbar directly). Also untested against an auto-hidden taskbar.
4. **`per_monitor` mode fetches assigned sources sequentially** (not parallelized),
   and doesn't use capture-time-sync scheduling (no single "the" source to learn a
   phase from when several are fetched per cycle — falls back to plain
   clock-boundary alignment). Worth revisiting if precise timing matters here too.
5. **No commits made to this repo yet** — everything is unstaged/untracked
   working-tree edits.
6. **`CUSTOM_IMAGERY_PLAN.md`'s Option B (satpy raw-composite) is explicitly
   deferred**, not abandoned.
7. **Overlay line support** (not just points/markers) — e.g. custom state/county
   borders, storm tracks, flight/shipping routes. `lonlat_to_pixels()` already
   supports arbitrary lon/lat arrays, so this is mostly "add a vector data source
   (GeoJSON/shapefile) and a polyline-drawing loop," same shape as `draw_graticule`
   generalized to arbitrary line strings instead of a fixed lat/lon grid.
8. **API/tool for lat/lon lookup** — `overlay_cities` entries need `lon`/`lat` typed
   in by hand. A geocoding lookup would remove that friction. Needs a data-source
   decision: bundled offline dataset (no network dependency, another thing to
   vendor/maintain) vs. a geocoding API call (network dependency, rate limits,
   offline behavior needs deciding).
9. **Configurable overlay icons** — `overlay_cities` currently always draws a plain
   circle marker. Custom per-marker icons would need an icon-path field on
   `CityMarker`, image loading/caching, and compositing at the projected pixel
   position (`Image.alpha_composite`, same pattern `draw_graticule` uses).
10. **Plugin interface for overlays**, so overlay content isn't limited to the
    hardcoded graticule/city-marker types — a registered provider could hit an API on
    every refresh cycle for genuinely dynamic content (live weather alerts, flight or
    ship positions, wildfire perimeters). Shape: an `OverlayProvider` protocol
    (`fetch(source, now) -> features`, `render(img, features, cfg) -> Image`) that
    `draw_overlays()` iterates over, configured via `[[overlay_plugins]]` (same shape
    as `[[combos]]`). Needs deciding: per-provider timeout/failure isolation (one
    broken API shouldn't break the whole update), caching/rate-limit handling, and
    whether providers need their own fetch cadence independent of the image refresh.
11. **Reduce prebaked config settings/magic numbers.** Accumulated a fair number of
    hardcoded numeric defaults, some duplicated, some empirically derived once and
    never revisited — worth an audit pass, especially before a second platform
    backend multiplies some of this. Examples: the overlay-sizing "reference width"
    scale factor (`w / 2000`) is duplicated independently in `draw_graticule` and
    `draw_city_markers`; `_GEOS_AREA_CONUS`, `trim_source_caption_frac`, and the info
    bar's minimum 28px height are one-off measurements without a documented
    re-derivation path; `_WALLPAPER_REGISTRY_CODES` and `_DESKTOP_WALLPAPER_POSITION`
    (`platform_windows.py`) are two separately-hardcoded dicts mapping the same style
    names to two different Windows-specific numeric schemes, easy to drift out of
    sync. Direction: centralize what must stay hardcoded as named constants with
    provenance comments, derive at runtime where feasible.
12. **Only the Windows backend exists and was tested.** `platform_base.
    WallpaperPlatform`'s abstract interface was only ever exercised through
    `WindowsPlatform` — there's no second implementation (real or stub) yet to
    confirm the interface is actually well-shaped for a genuinely different OS's
    constraints (Linux desktop environments don't have a single universal
    "set wallpaper" mechanism the way Windows does; some of the interface's
    assumptions, like a single taskbar height or per-monitor wallpaper support
    existing at all, may not hold everywhere).
13. **Wire power/network awareness into more places, and add reduced-frequency modes
    (not just binary skip/downgrade).** Currently `skip_on_battery` skips a cycle
    entirely and `metered_resolution` downgrades image size — both all-or-nothing.
    Worth adding: a `--loop` interval multiplier for battery/metered state instead of
    (or alongside) skipping cycles outright (`compute_next_run` scaling `interval`
    when `platform.get_power_state().on_battery`/`is_network_metered()`); a battery
    *percentage* threshold, not just "on battery at all" (`PowerState.
    battery_percent` is already plumbed through and unused for this); extending
    `metered_resolution`-style downgrading to other expensive operations (skipping
    overlay rendering, or reducing `per_monitor` mode to just its primary monitor);
    and applying both settings per-monitor in `per_monitor` mode instead of only as a
    whole-cycle skip.
14. **Lock screen support** — set the Windows lock screen image, not just the desktop
    wallpaper. Meaningfully more friction than desktop wallpaper, worth scoping
    carefully before starting:
    - The "proper" API is WinRT `Windows.System.UserProfile.LockScreen.
      SetImageFileAsync()` — but WinRT APIs touching user-profile/personalization
      state have historically required the calling process to have package identity
      (a packaged/MSIX app), which a plain unpackaged script or `pip`-installed
      console script doesn't have. Needs verifying whether this actually works from
      an unpackaged process on current Windows before assuming it's usable at all.
    - The fallback is the registry/Group Policy route
      (`HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Personalization`'s
      `LockScreenImage` value), which requires the process to run elevated (a real
      step up — everything else in this project runs as the logged-in user) and is
      Windows-edition-dependent (documented for Pro/Enterprise; Home is unverified).
    - Shape: a new `apply_lock_screen(path: Path) -> None` method on
      `WallpaperPlatform` (possibly paired with a `supports_lock_screen() -> bool`
      capability check), and an opt-in `set_lock_screen: bool = False` config field
      (opt-in specifically because of the elevation requirement).
    - Worth deciding up front whether the lock screen image should always mirror the
      desktop wallpaper, or could reasonably be a distinct combo/crop (a
      portrait-oriented crop makes more sense for a lock screen than the desktop's
      landscape cover-crop) — affects whether this reuses the existing render or
      needs its own pass.
