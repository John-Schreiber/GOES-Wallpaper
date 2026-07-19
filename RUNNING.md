# Running periodically

Pick one option, not both — running the built-in `--loop` mode alongside an OS
scheduler doubles the request rate to NOAA's CDN, and a second instance started
while the first is still running exits immediately with an error rather than
racing it for the same `wallpaper.jpg`/`state.json` (an OS-level advisory lock,
`goes_wallpaper.lock` in `data_dir`, held for the process's lifetime and
released automatically even on a crash).

## Option A: built-in `--loop` mode

```powershell
uv run python goes_wallpaper.py --loop
```

Runs indefinitely, sleeping until the next scheduled cycle (`interval_minutes`
in `config.toml`, default 5). Simplest option for a machine that's normally on
and logged in — start it once and leave it running: on Windows, from a shortcut
in your Startup folder; on KDE Plasma, add it to *System Settings → Startup and
Shutdown → Autostart* as a login script, working directory set to the repo (or
installed package's) location. Same tradeoff either way: one long-running
process, no external retry/restart if it crashes. Needs a live desktop session
either way — see the KDE backend's note in
[README.md](README.md#cross-platform) — same as Option C below.

## Option B: Windows Task Scheduler

Closer to how this script was originally run, and works well if you'd rather
Task Scheduler own retry/restart:

* **Trigger**: one-time trigger, then "repeat every X minutes/hours
  indefinitely." NOAA publishes a new CONUS image every 5 minutes.
* **Action**: start a program —
  * Program: `C:\path\to\GOES-Wallpaper-fork\.venv\Scripts\pythonw.exe`
  * Arguments: `goes_wallpaper.py`
  * Start in: `C:\path\to\GOES-Wallpaper-fork`

  Use the venv's `pythonw.exe`, not a system one — it's what `uv sync` actually
  installed the dependencies into. `pythonw.exe` runs without a console window.
  If you installed via `uv tool install` instead (see README.md's
  ["Without cloning"](README.md#without-cloning)), point Program at
  `goes-wallpaperw.exe` directly and leave Arguments/Start in blank.
* **Condition**: start only if a network connection is available.
* **Settings**: run as soon as possible after a missed start; don't start a new
  instance if one's already running; **run only when a user is logged on** —
  the KDE-session note above applies to Windows too, in that the script still
  needs a real desktop session to apply a wallpaper.

Add `--wait-for-sync` to the arguments to sleep once until shortly after the
next frame's learned publish time, instead of fetching immediately and relying
on `wait_for_fresh_capture`'s poll-and-retry loop. No-op until a phase has been
learned from a prior run; capped by `wait_for_sync_max_seconds`.

## Option C: Linux — systemd `--user` timer (KDE Plasma)

The recommended way to run this unattended-but-logged-in on KDE: a `oneshot`
service plus a timer, both installed as **user** units
(`~/.config/systemd/user/`, not `/etc/systemd/system/`). This matters: the KDE
backend talks to `plasmashell` over your login session's D-Bus bus, which only
exists once you're logged into a graphical session — a system-level service or
a plain cron job has no `DBUS_SESSION_BUS_ADDRESS` and can't reach it.

`~/.config/systemd/user/goes-wallpaper.service`:

```ini
[Unit]
Description=Update GOES satellite wallpaper

[Service]
Type=oneshot
WorkingDirectory=%h/path/to/GOES-Wallpaper-fork
ExecStart=%h/path/to/GOES-Wallpaper-fork/.venv/bin/python goes_wallpaper.py
```

(`%h` expands to your home directory. If you installed a release instead, point
`ExecStart` at `%h/.local/bin/goes-wallpaper --config %h/path/to/config.toml`
and drop `WorkingDirectory`.)

`~/.config/systemd/user/goes-wallpaper.timer`:

```ini
[Unit]
Description=Run goes-wallpaper.service on a schedule

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

`OnUnitActiveSec` sets the interval — match it to `interval_minutes` in
`config.toml`. `Persistent=true` catches up with one run after boot/login if a
scheduled run was missed while the session wasn't active. Add
`--wait-for-sync` to `ExecStart`'s arguments for the same reason as Option B.

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now goes-wallpaper.timer
```

Check status and logs with `systemctl --user status goes-wallpaper.timer` and
`journalctl --user -u goes-wallpaper.service`, in addition to the app's own
`log.txt`. These are user units, so they only run during an active login
session by default — exactly what the KDE backend already requires, so there's
nothing extra to configure. You do *not* need `loginctl enable-linger` (that's
for running user units with no active login at all, which the KDE backend can't
use anyway).
