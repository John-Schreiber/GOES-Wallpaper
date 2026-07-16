# Contributing

## Dev setup

```powershell
uv sync
uv run pytest
```

No network access or real hardware required — platform-specific logic is exercised
through a `FakePlatform` stub rather than real APIs. Not yet covered: `run_once`/
`run_loop` end-to-end (would need mocking `requests.Session` too), and
`platform_windows.py` itself, which is thin enough that manual verification against
real hardware has been the coverage so far.

## Adding a platform backend (Linux/macOS)

This is the contribution most likely to be useful right now. OS-specific behavior —
applying the wallpaper, screen/monitor detection, taskbar/dock avoidance, battery and
network-cost detection — lives entirely behind the `WallpaperPlatform` abstract
interface in `platform_base.py`. `goes_wallpaper.py` itself has no OS-specific code at
all; it only ever talks to a `WallpaperPlatform` instance.

To add a backend — Linux (any desktop environment) and macOS are both wanted, no
specific one prioritized over another:

1. Read `platform_base.WallpaperPlatform` — every abstract method has a docstring
   describing exactly what it needs to do and return.
2. Read `platform_windows.py` as the reference implementation. It's the only backend
   that exists today, and every method in it was validated against real hardware
   during development (see `NEXT_STEPS.md` for the specifics of how, including a
   couple of dead ends — a hand-rolled COM binding that didn't pan out — kept there
   deliberately so the reasoning isn't lost).
3. Implement a new `platform_<name>.py` with a class implementing every
   `WallpaperPlatform` method. It must not import from `goes_wallpaper.py` (that
   would be circular — `goes_wallpaper.py` imports `platform_base`, not the other way
   around); take plain primitives as parameters, not the app's `Config` object.
4. Add a branch for it in `platform_base.get_platform()`.
5. If your backend needs new dependencies, mark them with the right
   `sys_platform`/`platform_system` environment marker in `pyproject.toml` (see how
   `comtypes`/`winrt-*` are scoped to `sys_platform == 'win32'`) so other platforms'
   `uv sync` doesn't try to install them.
6. Not every method needs to be fully implementable on every OS — return a
   conservative default (e.g. `PowerState(on_battery=None)` for "can't detect this
   here") rather than raising, and say so in your method's docstring. The existing
   interface methods are written to treat "unknown" as "don't skip/downgrade," so a
   backend that can't detect something is still safe to ship.
7. Tests: follow the `FakePlatform` pattern in
   `tests/test_power_network_fallback.py` for anything that exercises
   `goes_wallpaper.py` logic against your backend's *shape* without needing your
   actual OS. Anything backend-specific (the real API calls) needs testing against
   real hardware — there's no way around that, and it's fine for that part to stay
   manual/undocumented-in-CI for now.

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
something concrete to pick up, and `CUSTOM_IMAGERY_PLAN.md` covers a larger
not-yet-started initiative (a from-scratch satellite-imagery compositor) if you want
something bigger.
