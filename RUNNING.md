# Running periodically

Pick one option, not both — for either platform, don't combine the built-in `--loop`
mode with an OS scheduler also invoking the script; they'll fight over the same
`wallpaper.jpg`/`state.json` and double the request rate to NOAA's CDN.

## Option A: built-in `--loop` mode

```powershell
uv run python goes_wallpaper.py --loop
```

Runs indefinitely, sleeping until the next scheduled cycle (`interval_minutes` in
`config.toml`, default 5). This is the simplest option for a machine that's normally
on and logged in — start it once and leave it running: on Windows, e.g. from a
shortcut in your Startup folder; on KDE Plasma, add it to *System Settings → Startup
and Shutdown → Autostart* as a "login script" running `uv run python
goes_wallpaper.py --loop` with the working directory set to the repo (or installed
package's) location — the direct Linux analogue of the Windows Startup-folder
approach, same tradeoffs (one long-running process, no external retry/restart
semantics if it crashes). Either way this still needs a live desktop session — see
the KDE backend's "requires a live desktop session" caveat in
[README.md](README.md#cross-platform) — that's true of Option C below too, not
something a scheduler works around.

## Option B: Windows Task Scheduler

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
  If you installed the package instead (see README.md's "Alternative: install as a
  package"), point Program at `goes-wallpaperw.exe` directly instead and leave
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

## Option C: Linux — systemd `--user` timer (KDE Plasma)

The Linux analogue of Task Scheduler, and the recommended way to run this
unattended-but-logged-in on KDE: a `oneshot` service plus a timer that repeatedly
activates it, both installed as **user** units (`~/.config/systemd/user/`, *not*
`/etc/systemd/system/`). This matters more here than it might sound: the KDE backend
talks to `plasmashell` over your login session's D-Bus bus, which only exists once
you're logged into a graphical session — a system-level service or a plain cron job
runs with no `DBUS_SESSION_BUS_ADDRESS` at all and can't reach it (see the
`platform_linux_kde` note in [README.md](README.md#cross-platform)).

`~/.config/systemd/user/goes-wallpaper.service`:

```ini
[Unit]
Description=Update GOES satellite wallpaper

[Service]
Type=oneshot
WorkingDirectory=%h/path/to/GOES-Wallpaper-fork
ExecStart=%h/path/to/GOES-Wallpaper-fork/.venv/bin/python goes_wallpaper.py
```

(`%h` expands to your home directory. If you installed the package instead — see
README.md's "Alternative: install as a package" — point `ExecStart` at
`%h/.local/bin/goes-wallpaper --config %h/path/to/config.toml` instead, since an
installed copy has no `config.toml` sitting next to it and doesn't need
`WorkingDirectory` set.)

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
`config.toml` (default 5, matching NOAA's CONUS publish cadence). `Persistent=true`
makes systemd catch up with one run after boot/login if a scheduled run was missed
while the session wasn't active (Task Scheduler's "run as soon as possible after a
missed start," equivalent). Add `--wait-for-sync` to `ExecStart`'s arguments for the
same reason as the Task Scheduler option above.

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now goes-wallpaper.timer
```

Check status and logs with `systemctl --user status goes-wallpaper.timer` and
`journalctl --user -u goes-wallpaper.service` — in addition to the app's own
`log.txt` in its data dir. Since these are user units, they only run while you have
an active login session (graphical or not) by default — exactly the constraint the
KDE backend already requires, so there's nothing extra to configure for that; you do
*not* need `loginctl enable-linger`, which is for running user units without any
active login, a mode the KDE backend can't use anyway since it needs the live
`plasmashell` session.
