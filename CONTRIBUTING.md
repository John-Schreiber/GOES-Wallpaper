# Contributing

## Dev setup

```powershell
uv sync
uv run pytest
```

No network access or real hardware required — platform-specific logic is
exercised through a `FakePlatform` stub. Not yet covered: `run_once`/`run_loop`
end-to-end, and the platform backends themselves (`platform_windows.py`,
`platform_linux_kde.py`, `platform_macos.py`), which get their default
single-screen path verified manually against real hardware instead — see
`NEXT_STEPS.md` items 11 (KDE) and 22 (macOS) for exactly what has and hasn't
been checked. `source_satpy.py` (`source_kind = "satpy_raw"`) has the same
split: pure band/scan-selection logic is unit tested
(`tests/test_source_satpy.py`), but real S3 access and satpy compositing need
`uv sync --extra satpy-raw` and a live `--render-to` run to verify.

## Adding a platform backend (GNOME/other)

OS-specific behavior — applying the wallpaper, screen/monitor detection,
taskbar/dock avoidance, battery and network-cost detection — lives entirely
behind the `WallpaperPlatform` abstract interface in `platform_base.py`.
`goes_wallpaper.py` has no OS-specific code; it only talks to a
`WallpaperPlatform` instance. Windows, KDE Plasma, and macOS already have
working backends — any other OS or desktop environment (GNOME, Cinnamon, XFCE,
etc.) is a welcome contribution, none prioritized over another.

To add one:

1. Read `platform_base.WallpaperPlatform` — every abstract method's docstring
   says exactly what it needs to do and return.
2. Read `platform_windows.py`, `platform_linux_kde.py`, and `platform_macos.py`
   as reference implementations — three independent takes on the same
   interface. Windows' methods are all confirmed against real hardware; KDE's
   and macOS's default single-screen paths are confirmed too, with
   `per_monitor` mode and battery/network detection still unit-test-only on
   both (`NEXT_STEPS.md` items 11 and 22) — a concrete example of documenting
   partial verification honestly instead of overclaiming coverage.
3. Implement a new `platform_<name>.py` with a class implementing every
   `WallpaperPlatform` method. It must not import from `goes_wallpaper.py`
   (that would be circular); take plain primitives as parameters, not the
   app's `Config` object.
4. Add a branch for it in `platform_base.get_platform()`. On Linux, follow
   `platform_linux_kde.py`'s pattern of sniffing `XDG_CURRENT_DESKTOP`/
   `XDG_SESSION_DESKTOP` for your desktop's name.
5. New Python dependencies go in `pyproject.toml` with the right
   `sys_platform`/`platform_system` marker (see how `comtypes`/`winrt-*` are
   scoped to `sys_platform == 'win32'`), so other platforms' `uv sync` skips
   them. Shelling out to external binaries instead (KDE's approach —
   `qdbus6`, `plasma-apply-wallpaperimage`, `upower`, `nmcli`) needs no
   `pyproject.toml` entry at all — just degrade gracefully if one's missing.
6. Not every method needs full support on every OS — return a conservative
   default (e.g. `PowerState(on_battery=None)`) rather than raising, and say so
   in the docstring. Callers already treat "unknown" as "don't skip/downgrade."
7. Tests: follow the `FakePlatform` pattern in
   `tests/test_power_network_fallback.py` for logic that needs your backend's
   *shape* without your actual OS, and `tests/test_platform_linux_kde.py`'s
   pattern (mocking `subprocess.run`/`shutil.which`) if you shell out to
   external tools. Anything that calls real OS APIs needs manual testing
   against real hardware — note in `NEXT_STEPS.md` exactly which paths were and
   weren't exercised live, the way items 11 and 22 do.

## Code style

- Match what's already there. Comments explain *why*, not *what*.
- No speculative abstractions or config knobs for hypothetical future needs —
  add what the current change actually requires.
- Prefer fixing root causes over working around them.

## Everything else

No formal process beyond "open a PR" — `main` is protected and requires one.
`NEXT_STEPS.md` has a running list of known gaps if you're looking for
something concrete, and `CUSTOM_IMAGERY_PLAN.md` covers a larger initiative
(`source_kind = "satpy_raw"`) if you want something bigger — its first cut has
landed; the doc's own status section lists what's still open.
