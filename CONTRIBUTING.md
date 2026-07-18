# Contributing

## Dev setup

```powershell
uv sync
uv run pytest
```

No network access or real hardware required — platform-specific logic is exercised
through a `FakePlatform` stub rather than real APIs. Not yet covered: `run_once`/
`run_loop` end-to-end (would need mocking `requests.Session` too), and the platform
backends themselves (`platform_windows.py`, `platform_linux_kde.py`, `platform_macos.py`),
which are thin enough that manual verification against real hardware has been the
coverage so far for all three, each at its default single-screen path — see
`NEXT_STEPS.md` item 11 for exactly what has and hasn't been checked against a real
KDE session, and item 22 for the same breakdown on macOS (`platform_macos.py`'s
own module docstring has the short version). `source_satpy.py`
(the `source_kind = "satpy_raw"` path) has the
same status for the same reason — its pure band/scan-selection logic is unit tested
(`tests/test_source_satpy.py`), but real S3 bucket access and satpy compositing need
`uv sync --extra satpy-raw` and a live fetch to verify (`--render-to`, see README's
"Tests" section, is the quickest way to do that without touching your desktop
wallpaper).

## Adding a platform backend (GNOME/other)

OS-specific behavior — applying the wallpaper, screen/monitor detection, taskbar/dock
avoidance, battery and network-cost detection — lives entirely behind the
`WallpaperPlatform` abstract interface in `platform_base.py`. `goes_wallpaper.py`
itself has no OS-specific code at all; it only ever talks to a `WallpaperPlatform`
instance. Windows, KDE Plasma, and macOS all have working backends already
(`platform_windows.py`, `platform_linux_kde.py`, `platform_macos.py` — all three now
have their default single-screen path confirmed on real hardware, with
multi-monitor/battery paths still unit-test-only on the latter two; see each
module's docstring and README's "Cross-platform" section). Any other OS or
desktop environment (GNOME, Cinnamon, XFCE, etc.) is welcome — none is prioritized
over another; pick whichever you actually use.

To add a backend:

1. Read `platform_base.WallpaperPlatform` — every abstract method has a docstring
   describing exactly what it needs to do and return.
2. Read `platform_windows.py` and `platform_linux_kde.py` as reference
   implementations — two independent takes on the same interface, useful for seeing
   which parts are genuinely OS-specific vs. incidental to one platform's APIs. Every
   method in `platform_windows.py` was validated against real hardware during
   development (see `NEXT_STEPS.md` for specifics, including a couple of dead ends —
   a hand-rolled COM binding that didn't pan out — kept there deliberately so the
   reasoning isn't lost); `platform_linux_kde.py`'s default single-screen path has
   real-hardware confirmation too, with `per_monitor` mode and a few other paths still
   only unit-tested (again, see `NEXT_STEPS.md` item 11) — a good concrete example of
   what "shipped but partially verified" looks like and how to document that honestly
   in your own PR rather than overclaiming full coverage. `platform_macos.py` is worth
   reading too, mainly for its coordinate-system handling (Cocoa's bottom-up
   `NSScreen` geometry flipped into this project's top-down `MonitorInfo` convention)
   and `NSWorkspace`'s per-screen API shape — its default single-screen path is now
   confirmed live on a real MacBook the same as the other two, though multi-monitor
   and battery-state paths are still unit-test-only (see `NEXT_STEPS.md` item 22).
3. Implement a new `platform_<name>.py` with a class implementing every
   `WallpaperPlatform` method. It must not import from `goes_wallpaper.py` (that
   would be circular — `goes_wallpaper.py` imports `platform_base`, not the other way
   around); take plain primitives as parameters, not the app's `Config` object.
4. Add a branch for it in `platform_base.get_platform()`. On Linux, follow
   `platform_linux_kde.py`'s pattern of sniffing `XDG_CURRENT_DESKTOP`/
   `XDG_SESSION_DESKTOP` for your desktop environment's name — see `NEXT_STEPS.md`
   item 17 for the known gap that this sniffing isn't configurable/overridable yet,
   worth keeping in mind (or fixing) if your backend hits the same issue.
5. If your backend needs new Python package dependencies, mark them with the right
   `sys_platform`/`platform_system` environment marker in `pyproject.toml` (see how
   `comtypes`/`winrt-*` are scoped to `sys_platform == 'win32'`) so other platforms'
   `uv sync` doesn't try to install them. If it instead shells out to external
   binaries (the KDE backend's approach — `qdbus6`/`qdbus`, `plasma-apply-
   wallpaperimage`, `upower`, `nmcli`), those aren't `pyproject.toml` dependencies at
   all; just degrade gracefully (log a warning, return a conservative default) when
   one isn't found, per the next point.
6. Not every method needs to be fully implementable on every OS — return a
   conservative default (e.g. `PowerState(on_battery=None)` for "can't detect this
   here") rather than raising, and say so in your method's docstring. The existing
   interface methods are written to treat "unknown" as "don't skip/downgrade," so a
   backend that can't detect something is still safe to ship.
7. Tests: follow the `FakePlatform` pattern in
   `tests/test_power_network_fallback.py` for anything that exercises
   `goes_wallpaper.py` logic against your backend's *shape* without needing your
   actual OS, and `tests/test_platform_linux_kde.py`'s pattern (mocking `subprocess.run`/
   `shutil.which` to fake each external tool's output) if your backend also shells out
   to external tools rather than calling OS APIs directly. Anything backend-specific
   (the real API/subprocess calls) needs testing against real hardware too — there's
   no way around that, and it's fine for that part to stay manual/undocumented-in-CI —
   but do note in `NEXT_STEPS.md` exactly which paths were and weren't exercised live,
   the way item 11 does for the KDE backend, rather than leaving it unstated.

## Code style

- Match what's already there. Comments explain *why*, not *what* — code should read
  clearly enough that a comment restating it would be redundant.
- No speculative abstractions or config knobs for hypothetical future needs — add
  what the current change actually requires.
- Prefer fixing root causes over working around them (see `NEXT_STEPS.md` for a
  couple of examples of this: the `.pyw`-extension import problem was worked around
  once for the test suite, then properly fixed by dropping `.pyw` entirely once the
  packaging work made the workaround's reason for existing go away).

## Everything else

There's no formal process beyond "open a PR" — `main` is protected and requires one.
`NEXT_STEPS.md` has a running list of known gaps and follow-ups if you're looking for
something concrete to pick up, and `CUSTOM_IMAGERY_PLAN.md` covers a larger initiative
(a from-scratch satellite-imagery compositor, `source_kind = "satpy_raw"`) if you want
something bigger — its first cut has landed, but the doc's own status section lists
what's still open (bandwidth/compute cost at a sustained `--loop` cadence, the B/A
hybrid fallback, real VIIRS night-lights).
