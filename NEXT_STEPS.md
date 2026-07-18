# Modernization status & next steps

Working notes for continuing `goes_wallpaper` development. This repo is a real
GitHub fork of https://github.com/pjlhjr/GOES-Wallpaper, substantially rewritten
since (see [CHANGELOG.md](CHANGELOG.md) for what's actually shipped — this file is
for forward-looking notes and open questions, not a second copy of the changelog).

For removing NOAA's baked-in state lines/city lights and adding custom overlays from
raw satellite data — a separate, bigger initiative than anything below — see
[CUSTOM_IMAGERY_PLAN.md](CUSTOM_IMAGERY_PLAN.md). A first cut (Option B,
`source_kind = "satpy_raw"`) has landed; see that doc for what's done vs. still
open.

## Suggested order of attack (as of 2026-07-16)

A recommended sequencing across the bug list and gap list below — not a commitment,
just what looks highest-leverage first:

1. **Bug 1 (satpy_raw disk leak)** — the only thing here that actively damages a
   user's machine over time; small fix, ship it first.
2. **Bugs 2–4** (side-docked taskbar, rotate-mode phase, atomic state writes) —
   each is a small, self-contained correctness fix; could be one PR.
3. **Gap 1 (long supervised `--loop` soak run)** — do this *after* the fixes above
   so the soak validates them too (the disk leak would have been caught by exactly
   this kind of run).
4. **Gap 15 → gap 9** (unify the overlay style config shape, then the
   `[[overlay_plugins]]` registry) — 15 is explicitly preparatory for 9, and 9
   unblocks gap 16 (per-combo overlays); doing them in that order avoids building
   the registry on three duplicated field families.
5. **Gap 11 + gap 17 (Linux backend + data_dir portability)** — the biggest
   audience-widener; 17 is a prerequisite discovered in this review (the default
   `data_dir` hardcodes Windows' AppData layout in the supposedly cross-platform
   core).
6. Everything else (lock screen, frozen exe, geocoding, icons) as interest dictates.

## Bug fixes needed (2026-07-16 full-repo review)

Found by code review of `goes_wallpaper.py`/`source_satpy.py`/`platform_windows.py`
(all 162 tests passing at the time). Ordered by severity:

1. ~~**`satpy_raw` band files accumulate forever in `satpy_raw_cache` — disk
   leak.**~~ Done: `fetch_composite` now deletes every file in `work_dir` that isn't
   part of the current scan's selection before downloading it, so peak usage stays
   at roughly one cycle's worth instead of growing forever. Regression-tested in
   `tests/test_source_satpy.py`.
2. **`avoid_taskbar` breaks for a side- or top-docked taskbar.**
   `WindowsPlatform.get_taskbar_height()` returns the `Shell_TrayWnd` window rect's
   *height* unconditionally. A left/right-docked taskbar's rect is the full screen
   height, so the info bar gets nudged up by ~the whole screen and composites at a
   negative y offset — verified Pillow 12 doesn't raise on that, it just renders the
   bar off-image, so those users silently get **no info bar** (with `avoid_taskbar`
   on by default). A top-docked taskbar nudges the bar up needlessly. Fix: use
   `SHAppBarMessage(ABM_GETTASKBARPOS)` to get the taskbar *edge*, and only apply the
   margin when it's docked at the bottom (return 0 otherwise).
3. **`combo_mode = "rotate"` schedules the next wake-up from the wrong combo's
   learned phase.** `run_loop` reads `state["last_source_key"]` — the combo *just
   fetched* — but the next cycle fetches the *next* combo in the rotation, whose
   publish phase (different satellite/sector/product) may differ. Fix: have rotate
   mode record the upcoming combo's key (it already persists
   `combo_rotation_index`), or compute the phase from that index in `run_loop`.
4. ~~**`state.json`/`wallpaper.json` writes aren't atomic.**~~ Done: both, plus the
   GeoJSON overlay cache sidecar, now go through a shared `_atomic_write_text`
   (write to a same-directory temp file, then `os.replace`).
5. ~~**Full Disk at `10848x10848` trips Pillow's decompression-bomb warning every
   cycle.**~~ Done: `Image.MAX_IMAGE_PIXELS` is now raised to 130M at module load,
   with a comment on why it's bounded rather than disabled.
6. ~~**Ctrl-C in `--loop` exits with a raw traceback.**~~ Done: `main()` now catches
   `KeyboardInterrupt` separately and exits with code 130 instead of a traceback.

## Security notes (2026-07-16 review)

No high-severity issues found. The trust model is sound: fetches are HTTPS with
`requests`' default TLS verification, the CDN response is content-type-checked
before decoding, `overlay_shell_command` is argv-only (no shell parsing), and the
release workflow's permissions are minimal (`contents: write` only). Worth
addressing or keeping in mind:

- **`config.toml` is a code-execution surface by design** — `overlay_shell_command`
  runs whatever argv the config specifies. That's the feature working as intended,
  but it means `--config` must never be pointed at an untrusted file, and the config
  shouldn't be writable by less-privileged users. Worth one sentence in the README's
  overlay section saying exactly that.
- **`_query_wmi_resolution` invokes `powershell` by bare name** (PATH lookup). In a
  hostile-PATH scenario that's hijackable. Cheap hardening: invoke via the absolute
  path (`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`) — it already
  passes `-NoProfile -NonInteractive`, which is the other half of that hardening.
- **Pillow decodes untrusted network bytes every cycle** — keep Pillow current
  (`uv.lock` pins it; `uv lock --upgrade-package pillow` periodically), and keep the
  decompression-bomb guard enabled when fixing bug 5 above.
- **`overlay_shell_command` stdout is read unbounded** (`capture_output=True`). A
  runaway/malicious provider process can exhaust memory before the timeout fires.
  Low priority (the command is already trusted config), but a size cap would make
  the failure mode graceful.
- **GitHub Actions are pinned by tag** (`actions/checkout@v4`, `astral-sh/setup-uv@v5`),
  not commit SHA. Tag-pinning trusts the action repo not to move the tag; SHA-pinning
  is the standard hardening if supply-chain risk matters here.
- ~~**`user_agent` still points at the upstream repo**~~ Done: now points at this
  fork (`+https://github.com/John-Schreiber/GOES-Wallpaper`).

## Verification notes worth knowing

A few non-obvious things learned while building and testing this, not really
"gaps" but easy to waste time rediscovering:

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
5. ~~**`CUSTOM_IMAGERY_PLAN.md`'s Option B (satpy raw-composite) is explicitly
   deferred**, not abandoned.~~ Done: its first cut (`source_kind = "satpy_raw"`)
   has landed — see that doc's status section for what's verified vs. still open
   (sustained-`--loop` bandwidth/compute cost, the B/A hybrid fallback, real VIIRS
   night-lights).
6. ~~**Overlay line support** (not just points/markers).~~ Done:
   `overlay_shell_command`/`overlay_geojson_files` + `_build_geojson_layer`
   (`goes_wallpaper.py`) draw `LineString`/`Polygon`/`Multi*` GeoJSON features
   (state/county borders, storm tracks, fire perimeters), not just points, via the
   same `lonlat_to_pixels()` projection `draw_graticule` uses.
7. **API/tool for lat/lon lookup** — city markers (`overlays/cities.geojson`, a
   `geojson_sources` entry — see `OVERLAYS.md`) need `lon`/`lat` typed in by hand. A
   geocoding lookup would remove that friction. Needs a data-source decision:
   bundled offline dataset (no network dependency, another thing to vendor/maintain)
   vs. a geocoding API call (network dependency, rate limits, offline behavior needs
   deciding).
8. **Configurable overlay icons** — `_build_geojson_layer` currently always draws a
   plain circle marker for Point/MultiPoint features. Custom per-marker icons would
   need an icon-path GeoJSON property (e.g. `properties.icon`, resolved the same way
   `properties.color`/`properties.name` already are), image loading/caching, and
   compositing at the projected pixel position (`Image.alpha_composite`, same
   pattern `draw_graticule` uses).
9. ~~**Plugin interface for overlays**~~ Partially done: `geojson_sources`/
   `shell_sources` (`overlays.toml`, see `OVERLAYS.md`) are now repeatable, named,
   independently-styled lists — multiple static GeoJSON file sets and/or multiple
   shell commands can run side by side, each with its own per-item try/except
   isolation (one broken source doesn't take the others down), closing the core gap
   this item described. Still open, if picked up: a live-HTTP provider kind (hit an
   API directly, not via a shelled-out script) with its own rate-limit handling, and
   whether providers need their own fetch cadence independent of the image refresh —
   today every source re-fetches/redraws exactly once per cycle, same as everything
   else. `OverlaysConfig`/`GeoJSONSource`/`ShellSource` (`goes_wallpaper.py`) are the
   landed shape; extending it with a third source *kind* (vs. more entries of the
   existing two kinds) is what remains of the original "plugin interface" framing.
10. ~~**Reduce prebaked config settings/magic numbers.**~~ Done: the overlay-sizing
    "reference width" scale factor is now the single `_OVERLAY_REFERENCE_WIDTH_PX`
    constant (`goes_wallpaper.py`), shared by `draw_graticule` and
    `_build_geojson_layer` instead of each hardcoding `w / 2000` independently; the info
    bar's minimum height is `_INFO_BAR_MIN_HEIGHT_PX` with a provenance comment
    (tuning floor, not a measurement); and `platform_windows.py`'s
    `_WALLPAPER_REGISTRY_CODES`/`_DESKTOP_WALLPAPER_POSITION` (two dicts mapping the
    same style names to two different numeric schemes) are merged into one
    `_WALLPAPER_STYLE_CODES` dict of `_StyleCodes` tuples, so the two schemes can't
    drift out of sync. `_GEOS_AREA_CONUS` and `trim_source_caption_frac` already
    carried adequate provenance/re-derivation comments and were left as-is.
11. **A second backend now exists and single-monitor wallpaper apply is verified on
    real hardware.** `platform_linux_kde.KDEPlatform` (KDE Plasma, via `qdbus`/
    `qdbus6` `evaluateScript` scripting and `plasma-apply-wallpaperimage`) was built
    from KDE's own docs and working community examples — see the module docstring
    for sources. A live run against a real Plasma session confirmed the default
    (single-screen, `combo_mode = "single"`) path end to end: `get_screen_size()`
    detection and `apply_wallpaper()` were exercised for real, and the desktop's
    `org.kde.image` config (queried directly via `qdbus6 ... evaluateScript`) showed
    it pointing at the freshly-rendered file after each run. Confirms the interface
    shape is workable for a second OS (`WallpaperPlatform`'s abstract methods all had
    a reasonable KDE implementation, including the "not every style is supported"
    escape hatch the docstring anticipated — KDE has no equivalent of Windows'
    `span`), but `per_monitor` combo mode, real multi-monitor geometry/assignment,
    panel-height detection against an actual panel, and `upower`/`nmcli` parsing on
    real hardware are all still outstanding — only exercised via the unit tests'
    mocked subprocess output so far, not a live multi-monitor/battery/metered-network
    setup. GNOME/other Linux DEs remain unimplemented — `platform_base.
    get_platform()` raises `NotImplementedError` for any `XDG_CURRENT_DESKTOP` that
    doesn't contain "kde".
12. **Wire power/network awareness into more places, and add reduced-frequency modes
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
13. **Lock screen support** — set the Windows lock screen image, not just the desktop
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
14. **A frozen standalone executable** (PyInstaller/Nuitka), so a non-technical
    Windows user could download and run without installing Python/uv at all.
    Explicitly backlogged behind the package-install path (`uv build`/`pip install .`/
    the GitHub Release wheel), which was the priority for the first release. Real risk
    worth flagging when this is picked up: the `winrt` packages use dynamic code
    generation/loading under the hood, which PyInstaller-style freezing sometimes
    doesn't handle cleanly — would need dedicated testing, possibly a documented
    fallback (skip power/network detection gracefully) if freezing that dependency
    turns out to be unreliable.
15. ~~**Improve config orthogonality/composability.**~~ Done, and went further than
    originally scoped: the three separately-prefixed copies of the overlay style
    shape (`overlay_city_*`/`overlay_shell_*`/`overlay_geojson_*`) are now one
    `GeoJSONSource`/`ShellSource` shape (color/line_width/marker_radius/opacity/
    font_size), each entry independently styled. City markers didn't just get
    re-styled onto the shared shape — `overlay_cities`/`CityMarker`/
    `draw_city_markers` were deleted outright; a city is now a `Point` feature with
    `properties.name` in a plain GeoJSON file (`overlays/cities.geojson`), drawn
    through the exact same `geojson_sources` path as any other static content, one
    fewer parallel mechanism rather than a fourth copy of the style shape. The
    composability half landed too (see item 9): `geojson_sources`/`shell_sources`
    are now repeatable lists, not one hardcoded slot each. Whole thing moved to its
    own file, `overlays.toml` (not `config.toml`), since it's content, not app
    behavior — see `OVERLAYS.md`. The broader pass over `Config` for the same
    prefix-family pattern elsewhere (`combo_*`/`source_crop_*`) is still open, and
    now scoped smaller since overlays (the biggest instance, 21 of ~70 fields) are
    out of `Config` entirely.
16. **Per-combo overlay scoping.** `overlays.toml` (`geojson_sources`/
    `shell_sources`/`graticule`) is loaded once and passed to every combo's render
    the same way — `EffectiveSource`/`Combo` carry no overlay fields at all, unlike
    `satellite`/`sector`/`product`/`resolution`, which each combo *can* override
    (`combo.satellite or cfg.satellite`, see `resolve_source()`). So in `"rotate"`/
    `"per_monitor"` mode, every combo gets the exact same overlays today — there's no
    way to say "GOES18 CONUS GEOCOLOR gets city markers" and "GOES19 CONUS Band 13
    gets the live storm-track overlay" as two different combos, only all-or-nothing.
    - **Decided: additive, not override.** The overlays.toml config stays a *global*
      overlay set that always applies to every combo (today's behavior, unchanged —
      `combo_mode = "single"` or any combo that doesn't care about overlays needs
      zero new config). Each `Combo` can *additionally* carry its own extra overlay
      content that layers on top *only* for that specific combo — e.g. every combo
      gets the global graticule, but only the GOES19 storm-track combo also gets
      that particular shell source's output composited on top of it. Not a
      per-combo override/replacement of the global set — both draw, global first,
      combo-specific second.
    - The config shape decision this needs got easier since the multi-source work
      (item 9): a combo could carry a list of `geojson_sources`/`shell_sources`
      *names* to additionally draw (referencing entries already defined in
      `overlays.toml`), rather than needing its own inline style fields — avoids
      re-opening the field-family duplication item 15 just fixed. Still needs
      deciding how a combo-specific reference composes with the global set in the
      cache key (each `GeoJSONSource`'s cache entry is already keyed on that
      source's own `name`, so a combo-specific *additional* source composites as its
      own independent cache entry, layered on top — no new cache-key work needed
      there, unlike when this item was originally scoped against the old
      single-slot-per-provider shape).
17. ~~**`DEFAULT_DATA_DIR` hardcodes Windows' AppData layout in the cross-platform
    core.**~~ Done: `WallpaperPlatform` gained `default_data_dir()`/
    `default_font_path()` abstract methods (implemented in `WindowsPlatform`), and
    `load_config(..., platform=...)` — as called from `main()` — prefers those over
    Config's own Windows-flavored class-level defaults whenever config.toml/CLI
    don't set `data_dir`/`info_font_path` explicitly. Config's class-level defaults
    are unchanged (still Windows paths) since they're what direct `Config()`
    construction — most of the test suite — relies on; a future Linux/macOS backend
    only needs to implement the two new methods, not touch Config or its defaults.
18. **GeoJSON overlay providers aren't area-aware.** `geojson_sources`/
    `shell_sources` call `lonlat_to_pixels(satellite, ...)` directly, so on a
    `satpy_raw` Full Disk/Mesoscale frame — where `graticule` *does* work via the
    real per-frame `AreaInfo` — the GeoJSON providers silently draw nothing (already
    noted in `draw_overlays`' docstring). Thread `area` down through
    `_build_geojson_layer`/`_draw_lonlat_run` the same way `draw_graticule` takes
    it. Cache-key note: `_geojson_files_cache_key`/`_cache_id` would then need the
    area extent in the key (satellite alone no longer identifies the projection once
    Full Disk and CONUS frames both render).
19. ~~**Nothing prunes stale `overlay_geojson_cache_*.png` entries** in
    `data_dir`.~~ Done: `prune_stale_geojson_cache` (called once per cycle from
    each `run_once*`) deletes an `overlay_geojson_cache_<id>.png`/`.json` pair
    once it's gone unused for `overlay_cache_max_age_days` (30 by default; 0
    disables it). "Unused" is tracked by mtime — `render_static_geojson_overlay`
    now touches both files on every cache *hit*, not just on rebuild, so an
    entry a running config still matches every cycle never goes stale no matter
    how old its content is; only an orphaned identity (a removed/renamed source,
    or one that changed satellite/resolution/style) ages out. Still not folded
    into gap 16's per-combo cache-key work — the pruning is identity-agnostic,
    so it'll cover whatever shape that work lands on without changes.
20. ~~**Platform selection is hardcoded, not configurable.**~~ Done: `platform`
    config setting (`"auto"` default, or explicit `"windows"`/`"kde"`/`"render"`)
    short-circuits `get_platform()`'s `sys.platform`/`XDG_CURRENT_DESKTOP` sniffing.
    config.toml only — no CLI flag yet. `"render"` (`platform_render.
    RenderOnlyPlatform`) is a third, non-hardware-backed option added alongside this:
    every method is a fixed fallback or a no-op (never applies a desktop wallpaper),
    for headless boxes/containers/CI where only the rendered image (`render_to`)
    matters — see README's "Render-only backend" section. Unlike `"windows"`/`"kde"`,
    it's never chosen by `"auto"`. Its fallback render size is configurable via the
    same `screen_width`/`screen_height` config already used for real backends'
    overrides — `get_platform()` forwards them into `RenderOnlyPlatform`'s
    constructor (as `render_fallback_width`/`height`) specifically so
    `list_monitors()` can honor them too, since that method (unlike
    `get_screen_size()`) has no per-call size parameters to take an override
    through.
21. **Reprojection (`output_projection`) is low quality: nearest-neighbor only, and
    warps already-drawn overlays instead of redrawing them.** Two related issues in
    `reproject_frame`, both visible in `PROJECTIONS.md`'s gallery:
    - No anti-aliasing at the valid-data/black boundary in `"orthographic"`/
      `"lambertazimuthal"` — renders visibly stair-stepped rather than a clean curve.
    - `draw_overlays` (graticule, city markers/labels, GeoJSON/shell-command
      features) runs *before* `reproject_frame` in the fetch pipeline, so overlay
      pixels get dragged through the same nearest-neighbor warp as the base image
      instead of being reprojected as geometry — thin lines can break into dashed
      segments, markers can distort, text can shear, worst near the projection's
      edges. `lambertconformal`/`platecarree` over a CONUS-sized box barely show
      this; `orthographic`/`lambertazimuthal` show it the most.
    Cheapest fix: supersample (render larger, downsample with antialiasing after
    reprojecting) — helps both issues without restructuring the pipeline. More
    thorough fix: reproject overlay *geometry* (lon/lat → destination-projection
    pixels) directly instead of warping pixels already drawn in the source grid —
    would also want `pyresample`/similar for the base-image resampling at that
    point, since it'd be adding a real dependency anyway.
22. **A third backend now exists (`platform_macos.MacOSPlatform`) and single-monitor
    wallpaper apply is verified on real hardware**, the same milestone item 11
    documents for the KDE backend. A live run on a real MacBook with a single
    (built-in) display confirmed the default (`combo_mode = "single"`) path end to
    end: `get_screen_size()` detection, the Cocoa-bottom-up-to-top-down coordinate
    flip, and `apply_wallpaper()`'s `NSWorkspace.setDesktopImageURL_forScreen_
    options_error_` call and style mapping (including the "tile"/"span" → "fill"
    degradation) all behaved as documented, and `get_taskbar_height`'s
    `visibleFrame.origin.y` Dock-height reasoning matched the real Dock. Still
    outstanding, only exercised via the unit tests' mocked output so far, not live
    hardware: `list_monitors`/`apply_wallpaper_per_monitor` against real
    multi-monitor geometry (needs an external display), and `get_power_state`'s
    `pmset -g batt` parsing on battery (including the no-battery-present desktop-Mac
    case). Whoever picks this up next should run these remaining paths against a
    real Mac (ideally with an external monitor to exercise `list_monitors`/
    `apply_wallpaper_per_monitor`, and on battery to exercise `get_power_state`)
    and update `platform_macos.py`'s module docstring plus README's "macOS backend"
    section with what's actually confirmed, the same way item 11 documents KDE's
    remaining verification gaps.
