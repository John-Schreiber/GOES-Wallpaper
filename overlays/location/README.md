# "You are here" — an optional location overlay

Three example [[shell_sources]] commands (see [OVERLAYS.md](../../OVERLAYS.md)),
one per OS, each printing a single GeoJSON `Point` at the computer's current
location, via that platform's own geolocation service — not a third-party
web API, and no coordinates hardcoded or stored in this repo:

| OS | Script | Backend | Extra install |
|---|---|---|---|
| Windows | [`windows.py`](windows.py) | WinRT `Windows.Devices.Geolocation` | `pip install winrt-windows-devices-geolocation` |
| macOS | [`macos.py`](macos.py) | [CoreLocationCLI](https://github.com/fulldecent/corelocationcli) | `brew install corelocationcli` |
| Linux | [`linux.py`](linux.py) | [GeoClue2](https://gitlab.freedesktop.org/geoclue/geoclue) via `gdbus` | none (uses glib2, already present) |

None of these are wired into `goes_wallpaper.py`'s own dependencies or install
extras — they're opt-in examples you point `shell_sources` at, same as the
hypothetical `fetch_storms.py` OVERLAYS.md's shell_sources section describes.
Only run the one matching your OS.

**Windows is confirmed working against real hardware** — `windows.py` returned
a real position fix on a real Windows machine, after fixing an initial
"no error, no output" report that turned out to be `get_geoposition_async()`
hanging indefinitely at High accuracy (no GPS on a desktop); it now requests
Default accuracy (network/Wi-Fi-based) and has a hard timeout, so a real
positioning problem fails with a message instead of hanging silently. **macOS
and Linux are still unverified** — no Mac or Linux desktop with a GeoClue2
session was available while writing these. Each script's own docstring says
exactly what's documented-but-unconfirmed about it, and what to check first if
it doesn't work. Treat "should work per the platform's own docs" differently
from this project's actually-hardware-verified paths (see `NEXT_STEPS.md` for
what those are).

## Usage

Pick the script for your OS and add it as a `[[shell_sources]]` entry in your
`overlays.toml`:

```toml
[[shell_sources]]
name = "here"
command = ["python", "overlays/location/windows.py"]   # or macos.py / linux.py
timeout = 30.0   # windows.py has its own internal 20s timeout waiting for a position fix -- this must exceed that
marker_radius = 10
```

Each script sets `properties.marker-symbol = "marker"` and `properties.name =
"Here"` on the point it prints — edit either script if you want a different
bundled icon (see `overlays/icons` for the full list) or label.

## Why every cycle, and is that a problem?

`shell_sources` re-runs its command every wallpaper-refresh cycle (no
independent fetch cadence — see OVERLAYS.md), so these scripts get invoked as
often as everything else, by default every few minutes. That's fine: all three
backends query the OS's own already-running location service (which typically
caches/reuses a recent fix rather than forcing a fresh GPS lookup on every
call), not a metered API — there's no meaningful cost difference from querying
less often, so no extra caching was built for this.
