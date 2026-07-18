# goes_wallpaper.py -- update the desktop wallpaper from a GOES satellite image
#
# Run directly (`uv run python goes_wallpaper.py` / `pythonw.exe goes_wallpaper.py`
# for no console window), or via the installed `goes-wallpaper`/`goes-wallpaperw`
# entry points (see pyproject.toml) once packaged.
#
# Copyright (C) 2026 John-Schreiber
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Originally based on pjlhjr/GOES-Wallpaper (Apache License 2.0); substantially
# rewritten since. See ATTRIBUTION.md for the full history and third-party notices.

"""Download the latest GOES satellite image and set it as the desktop wallpaper.

Fetches an image from NOAA STAR's GOES CDN (https://cdn.star.nesdis.noaa.gov), crops it
to the exact screen resolution, optionally overlays an info block with capture metadata,
and applies it as the wallpaper. Configurable via CLI flags and/or a TOML config file.

OS-specific operations (applying the wallpaper, screen/monitor detection, taskbar/dock
avoidance, power/network state) live behind platform_base.WallpaperPlatform — see that
module for the interface and platform_windows.py for the (currently only) backend.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import random
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import numpy as np
import requests
from PIL import Image, ImageColor, ImageDraw, ImageFont
from pyproj import CRS, Transformer
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from platform_base import MonitorInfo, WallpaperPlatform, WALLPAPER_STYLE_NAMES, get_platform

# Full Disk's largest published tier (10848x10848 = ~117.7M px) exceeds Pillow's
# default MAX_IMAGE_PIXELS (~89.5M), which logs a DecompressionBombWarning on every
# such fetch and would hard-error if Pillow's 2x safety threshold ever tightens.
# Raised to a bounded value that comfortably covers every known NOAA tier -- not
# disabled (None), since the guard is real protection against a compromised or
# misbehaving CDN serving an oversized image.
Image.MAX_IMAGE_PIXELS = 130_000_000

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_DATA_DIR = Path.home() / "AppData" / "Local" / "GOES-Wallpaper"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.toml")

# Human-friendly labels for known satellites/sectors, used only for the info block.
SATELLITE_LABELS = {
    "GOES16": "GOES-16",
    "GOES18": "GOES-18 (West)",
    "GOES19": "GOES-19 (East)",
}
SECTOR_LABELS = {
    "CONUS": "Continental US",
    "FD": "Full Disk",
    "M1": "Mesoscale 1",
    "M2": "Mesoscale 2",
}


def build_image_url(satellite: str, sector: str, product: str, resolution: str) -> str:
    base = f"https://cdn.star.nesdis.noaa.gov/{satellite}/ABI/{sector}/{product}"
    name = "latest.jpg" if resolution == "latest" else f"{resolution}.jpg"
    return f"{base}/{name}"


@dataclass(slots=True)
class Combo:
    """A named source+crop combo for combo_mode = "rotate"/"per_monitor". Any source
    field left unset (None) falls back to the top-level Config value; the crop fields
    always apply (default: no crop). `monitor` (0-based, matching the enumeration
    order IDesktopWallpaper reports) is required for "per_monitor" and ignored for
    "rotate"."""
    name: str
    satellite: str | None = None
    sector: str | None = None
    product: str | None = None
    resolution: str | None = None
    source_kind: str | None = None  # "cdn_jpg" | "satpy_raw"; falls back to Config.source_kind
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 1.0
    crop_bottom: float = 1.0
    # Lon/lat crop-box override -- unlike crop_left/top/right/bottom above, unset
    # (None) here falls back to Config.source_crop_min_lon/etc rather than always
    # applying; see Config.source_crop_min_lon.
    crop_min_lon: float | None = None
    crop_min_lat: float | None = None
    crop_max_lon: float | None = None
    crop_max_lat: float | None = None
    monitor: int | None = None


@dataclass(slots=True)
class CityMarker:
    """A labeled point for overlay_cities (georeferenced overlay; CONUS and Full Disk
    on the cdn_jpg path, any sector via satpy_raw's real per-frame AreaInfo)."""
    name: str
    lon: float
    lat: float


@dataclass(slots=True)
class Config:
    # Source image selection
    satellite: str = "GOES19"
    sector: str = "CONUS"
    product: str = "GEOCOLOR"
    # NOAA serves several discrete sizes per sector, not an arbitrary resize — verified
    # for CONUS: 625x375, 1250x750, 2500x1500, 5000x3000, 10000x6000 (the last is
    # native ABI band-2 resolution). "latest" resolves to 5000x3000 for CONUS, one tier
    # below the true max. Default here is 5000x3000 so a full-frame crop already covers
    # a 4K (3840x2160) monitor without upsampling; bump to "10000x6000" if you crop
    # aggressively (source_crop_*/combo crop_*) and need more headroom, at the cost of
    # a much bigger download every cycle. Full Disk's tiers are different (verified:
    # 1808x1808, 5424x5424, 10848x10848); Mesoscale wasn't verified — check with curl
    # against a few candidate WxH values before relying on a specific size there.
    resolution: str = "5000x3000"

    # "cdn_jpg" (default): today's behavior, fetch NOAA STAR's pre-rendered JPG.
    # "satpy_raw": fetch raw ABI L1b bands from the public noaa-goes16/18/19 S3
    # buckets and composite our own GeoColor-style image via satpy (see
    # source_satpy.py) — no baked-in state lines/logo/fake city lights, and real
    # georeferencing for any sector (not just CONUS). Requires the optional
    # `satpy-raw` install extra; `product` and `resolution` are ignored for this
    # source_kind (satpy always builds from a fixed band set, resampled to a fixed
    # target resolution — there's no NOAA product-code or JPG-size-tier
    # equivalent). Meaningfully heavier per cycle than cdn_jpg — see README.md's
    # "Custom raw-data source (satpy_raw)" section before enabling on a `--loop`.
    # See CUSTOM_IMAGERY_PLAN.md for the full design rationale.
    source_kind: str = "cdn_jpg"

    # Which platform_base.WallpaperPlatform backend to use. "auto" (default)
    # detects from sys.platform / XDG_CURRENT_DESKTOP, same as always -- explicit
    # "windows"/"kde" short-circuit that detection, e.g. for a KDE session whose
    # XDG_CURRENT_DESKTOP isn't set reliably. See platform_base.get_platform().
    platform: str = "auto"  # "auto" | "windows" | "kde"

    # Output
    # This class-level default is Windows-specific and only applies when Config is
    # constructed directly (as most tests do). The real CLI entry point
    # (goes_wallpaper.main) goes through load_config(..., platform=...), which
    # prefers WallpaperPlatform.default_data_dir() instead -- see load_config's
    # docstring.
    data_dir: Path = DEFAULT_DATA_DIR
    wallpaper_style: str = "fill"  # fill | fit | stretch | tile | center | span
    # If set, also save the rendered frame(s) here and skip applying them as the
    # desktop wallpaper -- for testing a render (new source_kind, overlays, crop
    # settings) without touching the real wallpaper. combo_mode = "per_monitor"
    # writes one file per monitor, with `_monitor{i}` inserted before the extension.
    render_to: Path | None = None

    # Screen handling
    crop_to_screen: bool = True
    crop_anchor: float = 0.5  # 0.0 = top/left, 0.5 = center, 1.0 = bottom/right
    screen_width: int | None = None  # override auto-detection
    screen_height: int | None = None
    span_all_monitors: bool = False  # crop to the full virtual desktop instead of the
    # primary monitor; pair with wallpaper_style = "span" so Windows stretches the one
    # image across all displays instead of just mirroring it onto the primary.

    # GetSystemMetrics reports a fake 1024x768 when the process isn't attached to an
    # interactive window station (e.g. a "run whether user is logged on or not" task).
    # WMI reads the video driver's current mode directly and isn't affected by that, so
    # it's tried as an automatic fallback before falling back to the 1024x768 default
    # (only applies to the single-monitor case — span_all_monitors still needs an
    # explicit screen_width/screen_height override in that scenario).
    wmi_screen_size_fallback: bool = True

    # NOAA bakes a white caption strip (timestamp/satellite/band legend) into the
    # bottom of every source image. Trim it before our own crop/info-block so it
    # doesn't get randomly kept/cut by the cover-crop or collide with our overlay.
    trim_source_caption: bool = True
    trim_source_caption_frac: float = 0.02  # measured ~0.0187 on a 1500px-tall CONUS frame

    # Region-of-interest crop applied to the source image (after caption trim, before
    # the screen-fit cover-crop below). Fractions of the trimmed image, 0.0-1.0 each.
    # Defaults to the full frame (no-op). Useful for framing a sub-region instead of
    # letting the cover-crop's resize+crop discard it unpredictably, and to cut off
    # NOAA's logo watermark (bottom-left corner of every frame on this CDN) — note this
    # can only trim edges, it can't remove things baked in across the whole frame like
    # the state/country border lines NOAA overlays on every product.
    source_crop_left: float = 0.0
    source_crop_top: float = 0.0
    source_crop_right: float = 1.0
    source_crop_bottom: float = 1.0

    # Alternative to source_crop_left/top/right/bottom above: frame the region of
    # interest by a lon/lat bounding box instead of a pixel fraction. All four must be
    # set together (validate_lonlat_crop_bounds enforces this); when set, this takes
    # precedence over source_crop_left/top/right/bottom for the same source. Requires
    # georeferencing calibration for the resolved satellite/sector (CONUS/Full Disk on
    # cdn_jpg, any sector on satpy_raw) -- falls back to the fractional crop above,
    # logged, if calibration isn't available. See lonlat_box_to_crop_fraction.
    source_crop_min_lon: float | None = None
    source_crop_min_lat: float | None = None
    source_crop_max_lon: float | None = None
    source_crop_max_lat: float | None = None

    # Reproject the rendered frame into a different map projection instead of the
    # satellite's native GEOS view. "native" (default): no reprojection. Not
    # combo-overridable. See reproject_frame.
    #
    # Bounds-framed (use source_crop_min_lon/min_lat/max_lon/max_lat above, required in
    # these modes -- those bounds become the reprojected output's extent, replacing
    # rather than stacking with the region-of-interest crop):
    #   "platecarree"      -- equirectangular.
    #   "lambertconformal"  -- conformal conic; the standard choice for a mid-latitude
    #                         regional map (what NWS/NOAA's own CONUS maps use) --
    #                         negligible distortion for a CONUS-sized box, unlike
    #                         platecarree/mercator. Standard parallels default to 1/6
    #                         and 5/6 of the way up the box's latitude range (a common
    #                         rule of thumb); override with output_projection_lcc_lat1/
    #                         _lat2 below if you want specific ones.
    #
    # Center-framed (use output_projection_center_lon/_center_lat below, defaulting to
    # the resolved source's own satellite sub-point / the equator):
    #   "orthographic"      -- a globe view as seen from space; pixels beyond the
    #                         visible hemisphere render black.
    #   "lambertazimuthal"  -- equal-area azimuthal; shows nearly the whole globe (not
    #                         just the visible hemisphere) without Mercator's polar
    #                         blowup, at the cost of shape distortion far from center.
    output_projection: str = "native"
    output_projection_center_lon: float | None = None
    output_projection_center_lat: float | None = None
    output_projection_lcc_lat1: float | None = None
    output_projection_lcc_lat2: float | None = None

    # Georeferenced overlays drawn on the raw fetched frame, before any of our own
    # trim/crop/resize (so the pixel grid matches the calibration below). CONUS and
    # Full Disk only — the CONUS calibration constants (GEOS projection extent per
    # satellite) were derived from one real ABI L1b CONUS file per satellite and
    # validated against 10 known city landmarks (median error well under a pixel at
    # 2500x1500); Full Disk reuses satpy's own published extent (see
    # _GEOS_AREA_FULL_DISK). Mesoscale isn't supported — its extent isn't fixed, it
    # moves, so it can't be hardcoded the same way. Adds to, doesn't replace, NOAA's
    # own baked-in state lines — this can't remove those, see source_crop_* above.
    overlay_graticule: bool = False
    overlay_graticule_step_deg: float = 10.0
    overlay_graticule_color: tuple[int, int, int] = (255, 255, 0)
    overlay_graticule_opacity: int = 110  # 0-255
    overlay_cities: tuple[CityMarker, ...] = ()
    overlay_city_marker_radius: int = 5
    overlay_city_color: tuple[int, int, int] = (255, 60, 60)
    overlay_city_font_size: int = 18

    # A single external overlay provider: runs `overlay_shell_command` (an argv list,
    # not a shell string -- no shell parsing, so no shell-injection risk) once per
    # cycle and expects a GeoJSON FeatureCollection/Feature/geometry on stdout. Draws
    # whatever Point/LineString/Polygon (or Multi* variant) features it returns --
    # e.g. a script that fetches NHC storm tracks or NIFC fire perimeters and prints
    # GeoJSON. Empty tuple (the default) disables it. A non-zero exit code, a timeout,
    # or unparseable stdout is logged and skipped, same as the CONUS/calibration
    # checks above -- a broken provider must not break the whole update cycle.
    overlay_shell_command: tuple[str, ...] = ()
    overlay_shell_timeout: float = 10.0
    overlay_shell_color: tuple[int, int, int] = (0, 200, 255)
    overlay_shell_line_width: int = 2
    overlay_shell_marker_radius: int = 5
    overlay_shell_opacity: int = 200
    overlay_shell_font_size: int = 14  # for Point features carrying a `name` property

    # A static overlay provider: reads GeoJSON from each path in
    # `overlay_geojson_files` (unlike overlay_shell_command, no re-fetching -- these
    # are files on disk that don't change cycle to cycle), merges every file's
    # features, and draws them the same way overlay_shell_command's output is drawn.
    # The composited RGBA layer is cached in cfg.data_dir, keyed on each file's path +
    # mtime plus (satellite, frame size, style) -- so editing a file, changing
    # resolution/satellite, or changing overlay_geojson_* below invalidates the cache
    # automatically, but an unchanged config only re-parses/re-projects once instead
    # of every single cycle.
    overlay_geojson_files: tuple[str, ...] = ()
    overlay_geojson_color: tuple[int, int, int] = (255, 255, 255)
    overlay_geojson_line_width: int = 1
    overlay_geojson_marker_radius: int = 5
    overlay_geojson_opacity: int = 160
    overlay_geojson_font_size: int = 14  # for Point features carrying a `name` property

    # Multiple named source+crop combos (see the Combo dataclass), and how to use
    # them. combos are ignored entirely in "single" mode (the default — just the
    # top-level satellite/sector/product/resolution/source_crop_* fields above).
    #   "single"      - today's behavior; combos ignored.
    #   "rotate"      - cycle through combos one per cycle (index persisted in
    #                   state.json), applied as a single wallpaper like "single" mode.
    #   "per_monitor" - each combo's `monitor` index gets its own independently
    #                   rendered+applied wallpaper via Windows' per-monitor wallpaper
    #                   API. Every combo must set `monitor` in this mode.
    combos: tuple[Combo, ...] = ()
    combo_mode: str = "single"

    # Info block overlay
    info_block: bool = True
    info_block_height_frac: float = 0.055
    info_block_opacity: int = 160  # 0-255
    # Same caveat as data_dir above: this Windows-specific default only applies when
    # Config is constructed directly. load_config(..., platform=...) prefers
    # WallpaperPlatform.default_font_path() instead. Either way, a path that can't
    # be loaded degrades gracefully to Pillow's built-in default font (see
    # draw_info_block/_fit_info_bar_font), never raises.
    info_font_path: str = r"C:\Windows\Fonts\segoeui.ttf"
    # The desktop wallpaper renders full-screen behind the taskbar, so a bar drawn at
    # the very bottom edge gets clipped by it. When enabled, the info bar is nudged up
    # by the taskbar's actual current height (queried live, so it tracks the user's
    # taskbar size/DPI) instead of a guessed constant. Best-effort/primary-monitor only.
    avoid_taskbar: bool = True

    # Networking / retries
    timeout_connect: float = 10.0
    timeout_read: float = 30.0
    max_retries: int = 5
    backoff_factor: float = 1.5
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    user_agent: str = "goes-wallpaper/2.0 (+https://github.com/pjlhjr/GOES-Wallpaper)"

    # Scheduling (only used with --loop)
    loop: bool = False
    interval_minutes: int = 5
    align_to_clock: bool = True
    jitter_seconds: float = 3.0

    # NOAA doesn't publish a new frame exactly on the clock boundary — there's a
    # processing/CDN lag after each scan. `sync_to_capture_time` learns that lag from
    # each frame's actual capture time (Last-Modified) and schedules the *next* wake-up
    # shortly after the next frame should land, instead of guessing at the raw
    # boundary. `wait_for_fresh_capture` complements this within a single cycle: if the
    # download comes back with the same capture time as last time (the new frame just
    # hasn't posted yet), retry a few times instead of applying a stale image.
    sync_to_capture_time: bool = True
    capture_offset_buffer_seconds: float = 20.0  # CDN propagation cushion after the learned publish time
    wait_for_fresh_capture: bool = True
    fresh_retry_interval_seconds: float = 15.0
    max_fresh_wait_seconds: float = 90.0

    # For Task Scheduler-style single-shot use: if the trigger fires right at (or
    # shortly before) the clock boundary and we've already learned this source's
    # publish phase from a prior run, sleep once until shortly after the next frame
    # should land instead of fetching immediately and relying on
    # wait_for_fresh_capture's poll-and-retry loop above (which still runs as a
    # backstop if this wait target turns out to be a bit early). No-op until a phase
    # has been learned, and capped by wait_for_sync_max_seconds so a Task Scheduler
    # trigger interval that doesn't match interval_minutes can't hang the task for
    # most of a cycle. Harmless (near-zero-wait) with --loop too, since run_loop's own
    # inter-cycle scheduling already lands wake-ups at this same target.
    wait_for_sync_time: bool = False
    wait_for_sync_max_seconds: float = 150.0

    # Power/network-aware fallbacks for expensive operations (large image downloads,
    # per-monitor mode's multiple fetches per cycle). Both go through
    # WallpaperPlatform, which degrades to "unknown" (None) gracefully on
    # platforms/hardware that can't detect this — unknown is always treated as "not
    # constrained," never as a guess to skip/downgrade on. Both default off so
    # today's behavior is unchanged unless opted into.
    skip_on_battery: bool = False    # skip the whole cycle if running on battery power
    metered_resolution: str | None = None  # override `resolution` when the network is metered (None = no
    # override); a no-op for source_kind = "satpy_raw" sources, which have no smaller-tier download the
    # way NOAA's CDN JPGs have discrete size tiers

    # Misc
    skip_if_unchanged: bool = True
    log_level: str = "INFO"

    @property
    def image_url(self) -> str:
        return build_image_url(self.satellite, self.sector, self.product, self.resolution)

    @property
    def wallpaper_path(self) -> Path:
        return self.data_dir / "wallpaper.jpg"

    @property
    def metadata_path(self) -> Path:
        return self.data_dir / "wallpaper.json"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "log.txt"


def load_config(config_path: Path, overrides: dict[str, Any], platform: WallpaperPlatform | None = None) -> Config:
    """Build a Config from an optional TOML file, then apply CLI overrides on top.
    `platform`, if given, supplies the data_dir/info_font_path defaults when neither
    the TOML file nor overrides set them -- e.g. Windows' AppData layout means
    nothing on a future Linux/macOS backend (see WallpaperPlatform.default_data_dir/
    default_font_path). Left unset (as most tests do, constructing Config directly
    or calling load_config without a platform), Config's own class-level defaults
    apply, unchanged from before this existed."""
    values: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as f:
            values.update(tomllib.load(f))

    valid_fields = {f.name for f in fields(Config)}
    unknown = set(values) - valid_fields
    if unknown:
        raise ValueError(f"Unknown config key(s) in {config_path}: {', '.join(sorted(unknown))}")

    values.update({k: v for k, v in overrides.items() if v is not None})

    if platform is not None:
        values.setdefault("data_dir", platform.default_data_dir())
        values.setdefault("info_font_path", platform.default_font_path())

    if "data_dir" in values:
        values["data_dir"] = Path(values["data_dir"])
    if "render_to" in values:
        values["render_to"] = Path(values["render_to"])
    if "retry_statuses" in values:
        values["retry_statuses"] = tuple(values["retry_statuses"])
    if "overlay_graticule_color" in values:
        values["overlay_graticule_color"] = tuple(values["overlay_graticule_color"])
    if "overlay_city_color" in values:
        values["overlay_city_color"] = tuple(values["overlay_city_color"])
    if "overlay_shell_command" in values:
        values["overlay_shell_command"] = tuple(values["overlay_shell_command"])
    if "overlay_shell_color" in values:
        values["overlay_shell_color"] = tuple(values["overlay_shell_color"])
    if "overlay_geojson_files" in values:
        values["overlay_geojson_files"] = tuple(values["overlay_geojson_files"])
    if "overlay_geojson_color" in values:
        values["overlay_geojson_color"] = tuple(values["overlay_geojson_color"])
    if "combos" in values:
        combo_fields = {f.name for f in fields(Combo)}
        parsed = []
        for i, combo_dict in enumerate(values["combos"]):
            unknown_keys = set(combo_dict) - combo_fields
            if unknown_keys:
                raise ValueError(f"Unknown key(s) in combos[{i}]: {', '.join(sorted(unknown_keys))}")
            parsed.append(Combo(**combo_dict))
        values["combos"] = tuple(parsed)
    if "overlay_cities" in values:
        city_fields = {f.name for f in fields(CityMarker)}
        parsed = []
        for i, city_dict in enumerate(values["overlay_cities"]):
            unknown_keys = set(city_dict) - city_fields
            if unknown_keys:
                raise ValueError(f"Unknown key(s) in overlay_cities[{i}]: {', '.join(sorted(unknown_keys))}")
            parsed.append(CityMarker(**city_dict))
        values["overlay_cities"] = tuple(parsed)

    return Config(**values)


def validate_combos(cfg: Config) -> None:
    valid_modes = {"single", "rotate", "per_monitor"}
    if cfg.combo_mode not in valid_modes:
        raise ValueError(f"combo_mode must be one of {sorted(valid_modes)}, got {cfg.combo_mode!r}")
    if cfg.combo_mode == "single":
        return

    if not cfg.combos:
        raise ValueError(f'combo_mode = "{cfg.combo_mode}" requires at least one [[combos]] entry')

    names = [c.name for c in cfg.combos]
    if len(names) != len(set(names)):
        raise ValueError(f"combo names must be unique: {names}")

    if cfg.combo_mode == "per_monitor":
        missing = [c.name for c in cfg.combos if c.monitor is None]
        if missing:
            raise ValueError(
                f'combo_mode = "per_monitor" requires every combo to set `monitor`; '
                f"missing on: {missing}"
            )
        monitor_indices = [c.monitor for c in cfg.combos]
        if len(monitor_indices) != len(set(monitor_indices)):
            raise ValueError(f"combo `monitor` indices must be unique: {monitor_indices}")


_VALID_SOURCE_KINDS = {"cdn_jpg", "satpy_raw"}


def validate_source_kind(cfg: Config) -> None:
    if cfg.source_kind not in _VALID_SOURCE_KINDS:
        raise ValueError(f"source_kind must be one of {sorted(_VALID_SOURCE_KINDS)}, got {cfg.source_kind!r}")
    for combo in cfg.combos:
        if combo.source_kind is not None and combo.source_kind not in _VALID_SOURCE_KINDS:
            raise ValueError(
                f"combos[{combo.name!r}].source_kind must be one of {sorted(_VALID_SOURCE_KINDS)}, "
                f"got {combo.source_kind!r}"
            )


def _check_lonlat_bounds(label: str, min_lon: float | None, min_lat: float | None, max_lon: float | None, max_lat: float | None) -> None:
    values = (min_lon, min_lat, max_lon, max_lat)
    if all(v is None for v in values):
        return
    if any(v is None for v in values):
        raise ValueError(f"{label}: min_lon/min_lat/max_lon/max_lat must all be set together, or none of them")
    if min_lon >= max_lon:
        raise ValueError(f"{label}: min_lon ({min_lon}) must be less than max_lon ({max_lon})")
    if min_lat >= max_lat:
        raise ValueError(f"{label}: min_lat ({min_lat}) must be less than max_lat ({max_lat})")


def validate_lonlat_crop_bounds(cfg: Config) -> None:
    _check_lonlat_bounds(
        "source_crop_min_lon/min_lat/max_lon/max_lat",
        cfg.source_crop_min_lon, cfg.source_crop_min_lat, cfg.source_crop_max_lon, cfg.source_crop_max_lat,
    )
    for combo in cfg.combos:
        _check_lonlat_bounds(
            f"combos[{combo.name!r}].crop_min_lon/min_lat/max_lon/max_lat",
            combo.crop_min_lon, combo.crop_min_lat, combo.crop_max_lon, combo.crop_max_lat,
        )


_VALID_OUTPUT_PROJECTIONS = {"native", "platecarree", "lambertconformal", "orthographic", "lambertazimuthal"}
_BOUNDS_FRAMED_PROJECTIONS = {"platecarree", "lambertconformal"}


def validate_output_projection(cfg: Config) -> None:
    if cfg.output_projection not in _VALID_OUTPUT_PROJECTIONS:
        raise ValueError(
            f"output_projection must be one of {sorted(_VALID_OUTPUT_PROJECTIONS)}, got {cfg.output_projection!r}"
        )
    if cfg.output_projection == "lambertconformal":
        if (cfg.output_projection_lcc_lat1 is None) != (cfg.output_projection_lcc_lat2 is None):
            raise ValueError("output_projection_lcc_lat1/lcc_lat2 must both be set together, or neither")
        if cfg.output_projection_lcc_lat1 is not None and cfg.output_projection_lcc_lat1 >= cfg.output_projection_lcc_lat2:
            raise ValueError(
                f"output_projection_lcc_lat1 ({cfg.output_projection_lcc_lat1}) must be less than "
                f"output_projection_lcc_lat2 ({cfg.output_projection_lcc_lat2})"
            )
    if cfg.output_projection not in _BOUNDS_FRAMED_PROJECTIONS:
        return

    sources = cfg.combos if cfg.combos else [None]
    for combo in sources:
        min_lon = combo.crop_min_lon if combo and combo.crop_min_lon is not None else cfg.source_crop_min_lon
        min_lat = combo.crop_min_lat if combo and combo.crop_min_lat is not None else cfg.source_crop_min_lat
        max_lon = combo.crop_max_lon if combo and combo.crop_max_lon is not None else cfg.source_crop_max_lon
        max_lat = combo.crop_max_lat if combo and combo.crop_max_lat is not None else cfg.source_crop_max_lat
        label = f"combos[{combo.name!r}]" if combo else "source_crop_min_lon/min_lat/max_lon/max_lat"
        if None in (min_lon, min_lat, max_lon, max_lat):
            raise ValueError(
                f'output_projection = "{cfg.output_projection}" requires a complete lon/lat crop box '
                f"(source_crop_min_lon/min_lat/max_lon/max_lat, or a per-combo override) for {label}"
            )


_VALID_PLATFORMS = {"auto", "windows", "kde"}


def validate_platform(cfg: Config) -> None:
    if cfg.platform not in _VALID_PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(_VALID_PLATFORMS)}, got {cfg.platform!r}")


@dataclass(slots=True)
class EffectiveSource:
    """The fully-resolved satellite/sector/product/resolution/crop for one cycle —
    either the top-level Config (combo=None) or a Combo with its unset fields filled
    in from Config."""
    name: str
    satellite: str
    sector: str
    product: str
    resolution: str
    source_kind: str
    crop_left: float
    crop_top: float
    crop_right: float
    crop_bottom: float
    crop_min_lon: float | None
    crop_min_lat: float | None
    crop_max_lon: float | None
    crop_max_lat: float | None

    @property
    def image_url(self) -> str:
        return build_image_url(self.satellite, self.sector, self.product, self.resolution)

    @property
    def key(self) -> str:
        """Identifies this exact source for per-source state (ETag/capture-time/
        learned publish phase), so unrelated sources sharing one config never mix up
        each other's freshness tracking. `product`/`resolution` are meaningless for
        satpy_raw (no NOAA product code or JPG size tier), so they're left out of its
        key rather than embedding whatever unrelated cfg defaults happen to be set."""
        if self.source_kind == "satpy_raw":
            return f"{self.satellite}/{self.sector}/satpy_raw"
        return f"{self.satellite}/{self.sector}/{self.product}/{self.resolution}"

    def satellite_label(self) -> str:
        return SATELLITE_LABELS.get(self.satellite, self.satellite)

    def sector_label(self) -> str:
        return SECTOR_LABELS.get(self.sector, self.sector)


def resolve_source(cfg: Config, combo: Combo | None) -> EffectiveSource:
    if combo is None:
        return EffectiveSource(
            name="default",
            satellite=cfg.satellite,
            sector=cfg.sector,
            product=cfg.product,
            resolution=cfg.resolution,
            source_kind=cfg.source_kind,
            crop_left=cfg.source_crop_left,
            crop_top=cfg.source_crop_top,
            crop_right=cfg.source_crop_right,
            crop_bottom=cfg.source_crop_bottom,
            crop_min_lon=cfg.source_crop_min_lon,
            crop_min_lat=cfg.source_crop_min_lat,
            crop_max_lon=cfg.source_crop_max_lon,
            crop_max_lat=cfg.source_crop_max_lat,
        )
    return EffectiveSource(
        name=combo.name,
        satellite=combo.satellite or cfg.satellite,
        sector=combo.sector or cfg.sector,
        product=combo.product or cfg.product,
        resolution=combo.resolution or cfg.resolution,
        source_kind=combo.source_kind or cfg.source_kind,
        crop_left=combo.crop_left,
        crop_top=combo.crop_top,
        crop_right=combo.crop_right,
        crop_bottom=combo.crop_bottom,
        crop_min_lon=combo.crop_min_lon if combo.crop_min_lon is not None else cfg.source_crop_min_lon,
        crop_min_lat=combo.crop_min_lat if combo.crop_min_lat is not None else cfg.source_crop_min_lat,
        crop_max_lon=combo.crop_max_lon if combo.crop_max_lon is not None else cfg.source_crop_max_lon,
        crop_max_lat=combo.crop_max_lat if combo.crop_max_lat is not None else cfg.source_crop_max_lat,
    )


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def setup_logging(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        cfg.log_path,
        mode="a",
        maxBytes=1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    root.handlers.clear()
    root.addHandler(handler)


# --------------------------------------------------------------------------- #
# Networking
# --------------------------------------------------------------------------- #

def build_session(cfg: Config) -> requests.Session:
    """Session with connection-level retries (backoff on transient failures/HTTP 5xx/429)."""
    session = requests.Session()
    session.headers["User-Agent"] = cfg.user_agent

    retry = Retry(
        total=cfg.max_retries,
        connect=cfg.max_retries,
        read=cfg.max_retries,
        status=cfg.max_retries,
        backoff_factor=cfg.backoff_factor,
        status_forcelist=cfg.retry_statuses,
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to path via a same-directory temp file + os.replace, so a
    crash/power loss mid-write can never leave a truncated/corrupted file behind --
    the replace is a single filesystem-level rename, not a partial write in place."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def load_state(cfg: Config) -> dict[str, Any]:
    if cfg.state_path.exists():
        try:
            return json.loads(cfg.state_path.read_text())
        except (json.JSONDecodeError, OSError):
            logging.warning("Could not read state file, starting fresh")
    return {}


def save_state(cfg: Config, state: dict[str, Any]) -> None:
    _atomic_write_text(cfg.state_path, json.dumps(state, indent=2))


def fetch_image(cfg: Config, session: requests.Session, url: str, prev_etag: str | None) -> tuple[bytes, dict[str, str]] | None:
    """Download the image at url. Returns None if the server reports no change (304)."""
    headers = {}
    if prev_etag:
        headers["If-None-Match"] = prev_etag

    logging.info("Requesting %s", url)
    resp = session.get(
        url,
        headers=headers,
        timeout=(cfg.timeout_connect, cfg.timeout_read),
    )

    if resp.status_code == 304:
        logging.info("Server reports no change since last download (304)")
        return None

    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "image/jpeg" not in content_type:
        raise ValueError(f"Unexpected content-type: {content_type!r}")

    headers = {k.lower(): v for k, v in resp.headers.items()}
    return resp.content, headers


def parse_capture_time(headers: dict[str, str]) -> str | None:
    """Parse the source frame's actual capture time from the HTTP Last-Modified header."""
    last_modified = headers.get("last-modified")
    if not last_modified:
        return None
    try:
        return (
            datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    except ValueError:
        return None


def fetch_fresh_image(
    cfg: Config,
    session: requests.Session,
    url: str,
    prev_etag: str | None,
    prev_capture_time: str | None,
    deadline: float,
) -> tuple[bytes, dict[str, str]] | None:
    """Fetch the image at url, retrying briefly if the CDN is still serving the
    previous frame (same capture time as last cycle) so we don't apply stale content
    when a fresher one is expected imminently."""
    while True:
        result = fetch_image(cfg, session, url, prev_etag)
        if result is None:
            return None  # genuinely unchanged (304)

        content, headers = result
        if not cfg.wait_for_fresh_capture or prev_capture_time is None:
            return content, headers

        capture_time = parse_capture_time(headers)
        if capture_time is None or capture_time != prev_capture_time:
            return content, headers

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logging.info(
                "Still on previous capture (%s) after waiting; using it anyway", capture_time
            )
            return content, headers

        wait = min(cfg.fresh_retry_interval_seconds, remaining)
        logging.info(
            "Downloaded content but capture time is unchanged (%s); retrying in %.0fs",
            capture_time, wait,
        )
        time.sleep(wait)


# --------------------------------------------------------------------------- #
# Screen handling
# --------------------------------------------------------------------------- #

def trim_source_caption(img: Image.Image, frac: float) -> Image.Image:
    """Cut NOAA's own baked-in caption strip off the bottom of the source image."""
    width, height = img.size
    trim_px = round(height * frac)
    if trim_px <= 0:
        return img
    return img.crop((0, 0, width, height - trim_px))


def crop_fractional(img: Image.Image, left: float, top: float, right: float, bottom: float) -> Image.Image:
    """Crop to a region of interest *before* the screen-fit cover-crop, so that region
    (rather than the cover-crop's own resize+crop) controls what survives — e.g. to
    frame a sub-area, or trim NOAA's logo watermark."""
    if (left, top, right, bottom) == (0.0, 0.0, 1.0, 1.0):
        return img
    width, height = img.size
    return img.crop((round(left * width), round(top * height), round(right * width), round(bottom * height)))


def crop_to_screen(img: Image.Image, screen_size: tuple[int, int], anchor: float) -> Image.Image:
    """Resize+center-crop (CSS 'cover' style) so the image exactly fills the screen
    without the stretching/letterboxing that Windows' own scaling can introduce."""
    target_w, target_h = screen_size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    excess_x = new_w - target_w
    excess_y = new_h - target_h
    left = round(excess_x * anchor)
    top = round(excess_y * anchor)
    return resized.crop((left, top, left + target_w, top + target_h))


# --------------------------------------------------------------------------- #
# Georeferenced overlays (CONUS and Full Disk on the hand-calibrated cdn_jpg path)
# --------------------------------------------------------------------------- #

# GEOS projection extent (meters) for each satellite's CONUS sector, as served by
# NOAA STAR's CDN. Derived by loading one real ABI L1b CONUS radiance file per
# satellite with satpy and reading its area definition — not a resize/crop of the
# full-disk grid, this is the CONUS sector's own fixed extent (Mesoscale sectors move
# and can't be hardcoded this way). Validated by projecting 10 known city landmarks (5
# per satellite) and confirming they land on the correct city/coastline in a real
# fetched frame.
_GEOS_AREA_CONUS = {
    "GOES18": {"lon_0": -137.0, "extent": (-2505021.61, 1583173.65752, 2505021.61, 4589199.58952)},
    "GOES19": {"lon_0": -75.0, "extent": (-3627271.29128, 1583173.65752, 1382771.92872, 4589199.58952)},
}

# Full Disk's extent, unlike CONUS, isn't a windowed subset that had to be measured
# from a real file — it's ABI's entire fixed viewing geometry, which is identical for
# every GOES-R series satellite regardless of orbital slot (only lon_0 differs by
# slot). Reused here from satpy's own shipped area definitions
# (goes_east_abi_f_2km/goes_west_abi_f_2km in satpy/etc/areas.yaml) rather than
# re-derived, since that's the same constant satpy's GOES ABI L1b reader uses.
_GEOS_AREA_FULL_DISK = {
    "GOES18": {"lon_0": -137.0, "extent": (-5434894.885056, -5434894.885056, 5434894.885056, 5434894.885056)},
    "GOES19": {"lon_0": -75.0, "extent": (-5434894.885056, -5434894.885056, 5434894.885056, 5434894.885056)},
}

# Per-sector calibration lookup used by the hand-calibrated path (lonlat_to_pixels)
# — sectors absent here (Mesoscale) have no fixed extent to hardcode and stay
# unsupported for cdn_jpg; satpy_raw frames instead carry their own real per-frame
# AreaInfo (see lonlat_to_pixels_area) and aren't limited to this table.
_GEOS_AREA_BY_SECTOR = {"CONUS": _GEOS_AREA_CONUS, "FD": _GEOS_AREA_FULL_DISK}

# Overlay line widths/marker sizes below are tuned by eye at this frame width, then
# scaled proportionally for other resolutions (draw_graticule, draw_city_markers) —
# not a physical/measured constant, just the width the original tuning pass used.
_OVERLAY_REFERENCE_WIDTH_PX = 2000

_geos_transformers: dict[float, Transformer] = {}


def _geos_transformer(lon_0: float) -> Transformer:
    """Cached by lon_0 alone (not satellite/sector) since every GEOS transform used
    here shares the same h/ellps/sweep and only the sub-satellite longitude differs
    between satellites — CONUS and Full Disk calibrations for the same satellite
    share one transformer, just with a different extent applied afterward."""
    if lon_0 not in _geos_transformers:
        crs = CRS.from_dict({
            "proj": "geos", "sweep": "x", "lon_0": lon_0, "h": 35786023,
            "x_0": 0, "y_0": 0, "ellps": "GRS80", "units": "m",
        })
        _geos_transformers[lon_0] = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return _geos_transformers[lon_0]


def _project_to_pixels(
    transformer: Transformer, extent: tuple[float, float, float, float],
    lons: np.ndarray, lats: np.ndarray, img_w: int, img_h: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Shared linear fraction-of-extent -> pixel math, used by both lonlat_to_pixels
    (hand-calibrated CONUS/Full Disk, _GEOS_AREA_BY_SECTOR lookup) and
    lonlat_to_pixels_area (any sector, real AreaDefinition from a satpy_raw frame) --
    projection-agnostic as long as the source raster is a rectilinear GEOS-projected
    grid, which holds for both."""
    x, y = transformer.transform(lons, lats)
    x0, y0, x1, y1 = extent
    col = (x - x0) / (x1 - x0) * img_w
    row = (y1 - y) / (y1 - y0) * img_h
    return col, row


def lonlat_to_pixels(
    satellite: str, lons: np.ndarray, lats: np.ndarray, img_w: int, img_h: int, sector: str = "CONUS"
) -> tuple[np.ndarray, np.ndarray] | None:
    """Vectorized lon/lat -> (col, row) in a `sector` frame at img_w x img_h, for
    whichever resolution tier was actually fetched (fraction-of-extent based, not
    tied to a specific pixel count). Returns None for a sector/satellite combination
    without hardcoded calibration data (see _GEOS_AREA_BY_SECTOR)."""
    info = _GEOS_AREA_BY_SECTOR.get(sector, {}).get(satellite)
    if info is None:
        return None
    return _project_to_pixels(_geos_transformer(info["lon_0"]), info["extent"], lons, lats, img_w, img_h)


@dataclass(slots=True)
class AreaInfo:
    """Real georeferencing for one fetched frame, as reported by satpy's own
    AreaDefinition -- the satpy_raw-path equivalent of a _GEOS_AREA_BY_SECTOR lookup,
    but valid for any sector (including Mesoscale), not just the two hand-calibrated
    CONUS/Full Disk extents. Populated on FetchedFrame once a satpy_raw frame is
    actually loaded; unknown before then, so it lives on the frame, not on
    EffectiveSource."""
    proj4_params: dict[str, Any]
    extent: tuple[float, float, float, float]


_area_info_transformers: dict[tuple[float, ...], Transformer] = {}


def _area_transformer(area: AreaInfo) -> Transformer:
    """Cached forward (EPSG:4326 -> the area's own CRS) transformer for one real
    per-frame AreaInfo, keyed on its proj4 params."""
    cache_key = tuple(sorted(area.proj4_params.items()))
    if cache_key not in _area_info_transformers:
        crs = CRS.from_dict(area.proj4_params)
        _area_info_transformers[cache_key] = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return _area_info_transformers[cache_key]


def lonlat_to_pixels_area(
    area: AreaInfo, lons: np.ndarray, lats: np.ndarray, img_w: int, img_h: int
) -> tuple[np.ndarray, np.ndarray]:
    """Same math as lonlat_to_pixels, but sourced from a real AreaDefinition (any
    sector) instead of the hand-calibrated _GEOS_AREA_BY_SECTOR[sector][satellite]
    lookup."""
    return _project_to_pixels(_area_transformer(area), area.extent, lons, lats, img_w, img_h)


def _resolve_source_projection(
    satellite: str, sector: str, area: AreaInfo | None,
) -> tuple[Transformer, tuple[float, float, float, float]] | None:
    """Resolve (forward EPSG:4326->source-CRS transformer, source extent) for a
    frame -- the real per-frame `area` when given (satpy_raw, any sector), otherwise
    the hand-calibrated CONUS/Full Disk lookup (cdn_jpg). Returns None if there's no
    calibration for this satellite/sector combination. Used by reproject_frame, which
    (unlike lonlat_to_pixels/lonlat_to_pixels_area) needs the transformer/extent pair
    itself rather than just a projected result."""
    if area is not None:
        return _area_transformer(area), area.extent
    info = _GEOS_AREA_BY_SECTOR.get(sector, {}).get(satellite)
    if info is None:
        return None
    return _geos_transformer(info["lon_0"]), info["extent"]


def _satellite_lon_0(satellite: str, sector: str, area: AreaInfo | None) -> float | None:
    """The sub-satellite longitude for a frame -- used as output_projection's default
    orthographic center when output_projection_center_lon isn't set. None if there's
    no calibration/real projection info to read it from."""
    if area is not None:
        return area.proj4_params.get("lon_0")
    info = _GEOS_AREA_BY_SECTOR.get(sector, {}).get(satellite)
    return info["lon_0"] if info else None


def lonlat_box_to_crop_fraction(
    satellite: str, sector: str, area: AreaInfo | None, img_w: int, img_h: int,
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
) -> tuple[float, float, float, float] | None:
    """Convert a lon/lat bounding box to a (left, top, right, bottom) pixel-fraction
    crop box, using the same calibration draw_overlays uses: the real per-frame `area`
    when given (satpy_raw, any sector), otherwise the hand-calibrated CONUS/Full Disk
    lookup for `satellite`/`sector` (cdn_jpg). Returns None (crop should be skipped --
    caller falls back to a fractional crop) if there's no calibration for this
    satellite/sector combination.

    Projects all 4 corners of the box, not just min/max lon/lat independently -- GEOS
    projection is nonlinear, so a lon/lat rectangle doesn't generally map to an
    axis-aligned pixel rectangle. The bounding box of the 4 projected corners is the
    closest rectangular approximation, which is what crop_fractional needs; for a
    CONUS-sized box the curvature is negligible (same order as the sub-pixel median
    error the CONUS calibration itself was validated to), but a very large box (e.g.
    most of a Full Disk sector) will see this approximation include a bit more margin
    than the exact (curved) region would."""
    lons = np.array([min_lon, max_lon, max_lon, min_lon])
    lats = np.array([min_lat, min_lat, max_lat, max_lat])
    if area is not None:
        result = lonlat_to_pixels_area(area, lons, lats, img_w, img_h)
    else:
        result = lonlat_to_pixels(satellite, lons, lats, img_w, img_h, sector)
    if result is None:
        return None
    cols, rows = result
    if not (np.all(np.isfinite(cols)) and np.all(np.isfinite(rows))):
        return None
    left = float(np.min(cols)) / img_w
    right = float(np.max(cols)) / img_w
    top = float(np.min(rows)) / img_h
    bottom = float(np.max(rows)) / img_h
    return (
        max(0.0, min(1.0, left)), max(0.0, min(1.0, top)),
        max(0.0, min(1.0, right)), max(0.0, min(1.0, bottom)),
    )


# GRS80 semi-major axis (meters) -- same ellipsoid used throughout this module's GEOS
# calibration, and the natural "radius of the visible disk" for an orthographic view
# centered on a point at infinity (true orthographic, not perspective-from-GEOS-orbit).
_GRS80_SEMI_MAJOR_AXIS_M = 6378137.0


def _bounds_projected_extent(
    dst_crs: CRS, min_lon: float, min_lat: float, max_lon: float, max_lat: float, n: int = 25,
) -> tuple[float, float, float, float]:
    """The (x0, y0, x1, y1) extent a lon/lat box covers once projected into dst_crs.
    Samples an n x n grid across the whole box rather than just its 4 corners --
    conic/azimuthal projections curve parallels/meridians, so e.g. a box's bottom edge
    can bulge further out in projected y than either of its bottom corners do (verified
    empirically for lambertconformal: the bottom-edge midpoint's y was ~10% beyond the
    bottom corners' own y). Cheap: n=25 is 625 points, a single vectorized transform
    call."""
    lons, lats = np.meshgrid(np.linspace(min_lon, max_lon, n), np.linspace(min_lat, max_lat, n))
    transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    x, y = transformer.transform(lons, lats)
    return float(np.nanmin(x)), float(np.nanmin(y)), float(np.nanmax(x)), float(np.nanmax(y))


def reproject_frame(
    img: Image.Image, satellite: str, sector: str, area: AreaInfo | None,
    projection: str, bounds: tuple[float, float, float, float] | None,
    center_lon: float, center_lat: float, out_w: int, out_h: int,
    lcc_lat1: float | None = None, lcc_lat2: float | None = None,
) -> Image.Image | None:
    """Reproject img (drawn in its native GEOS pixel grid -- must run after
    draw_overlays/trim_source_caption, before any further crop/resize) into a
    different map projection via nearest-neighbor resampling. Pure pyproj + numpy, no
    pyresample/satpy dependency, so this works identically for cdn_jpg's
    hand-calibrated CONUS/Full Disk grid and satpy_raw's real per-frame AreaInfo.
    Returns None (reprojection skipped, caller falls back to the native-projection
    pipeline) if there's no calibration for this satellite/sector combination.

    Bounds-framed (`bounds` = min_lon, min_lat, max_lon, max_lat -- required, becomes
    the output's extent):
    `projection = "platecarree"`: equirectangular.
    `projection = "lambertconformal"`: conformal conic, standard parallels `lcc_lat1`/
    `lcc_lat2` (default: 1/6 and 5/6 up the box's latitude range if not given).

    Center-framed (`center_lon`/`center_lat, `bounds` unused):
    `projection = "orthographic"`: a globe view as seen from space; pixels beyond the
    visible hemisphere (non-finite after the inverse transform) render black -- this
    is "space", not a bug.
    `projection = "lambertazimuthal"`: equal-area azimuthal, valid out to (not
    including) the antipode -- shows nearly the whole globe rather than just the
    visible hemisphere."""
    resolved = _resolve_source_projection(satellite, sector, area)
    if resolved is None:
        return None
    src_transformer, src_extent = resolved
    src_w, src_h = img.size

    if projection == "platecarree":
        min_lon, min_lat, max_lon, max_lat = bounds
        dst_lons = np.linspace(min_lon, max_lon, out_w)
        dst_lats = np.linspace(max_lat, min_lat, out_h)  # row 0 = north (max_lat)
        lon_grid, lat_grid = np.meshgrid(dst_lons, dst_lats)
    elif projection == "lambertconformal":
        min_lon, min_lat, max_lon, max_lat = bounds
        lon_0, lat_0 = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
        span = max_lat - min_lat
        lat_1 = lcc_lat1 if lcc_lat1 is not None else min_lat + span / 6
        lat_2 = lcc_lat2 if lcc_lat2 is not None else max_lat - span / 6
        lcc_crs = CRS.from_dict({
            "proj": "lcc", "lon_0": lon_0, "lat_0": lat_0, "lat_1": lat_1, "lat_2": lat_2, "ellps": "GRS80",
        })
        x0, y0, x1, y1 = _bounds_projected_extent(lcc_crs, min_lon, min_lat, max_lon, max_lat)
        xs = np.linspace(x0, x1, out_w)
        ys = np.linspace(y1, y0, out_h)  # row 0 = north (max y)
        x_grid, y_grid = np.meshgrid(xs, ys)
        inverse = Transformer.from_crs(lcc_crs, "EPSG:4326", always_xy=True)
        with np.errstate(invalid="ignore"):
            lon_grid, lat_grid = inverse.transform(x_grid, y_grid)
    elif projection == "orthographic":
        ortho_crs = CRS.from_dict({"proj": "ortho", "lon_0": center_lon, "lat_0": center_lat, "ellps": "GRS80"})
        inverse = Transformer.from_crs(ortho_crs, "EPSG:4326", always_xy=True)
        r = _GRS80_SEMI_MAJOR_AXIS_M
        xs = np.linspace(-r, r, out_w)
        ys = np.linspace(r, -r, out_h)  # row 0 = top (+r)
        x_grid, y_grid = np.meshgrid(xs, ys)
        with np.errstate(invalid="ignore"):
            lon_grid, lat_grid = inverse.transform(x_grid, y_grid)
    elif projection == "lambertazimuthal":
        laea_crs = CRS.from_dict({"proj": "laea", "lon_0": center_lon, "lat_0": center_lat, "ellps": "GRS80"})
        inverse = Transformer.from_crs(laea_crs, "EPSG:4326", always_xy=True)
        r = 2 * _GRS80_SEMI_MAJOR_AXIS_M  # valid up to (not including) the antipode
        xs = np.linspace(-r, r, out_w)
        ys = np.linspace(r, -r, out_h)  # row 0 = top (+r)
        x_grid, y_grid = np.meshgrid(xs, ys)
        with np.errstate(invalid="ignore"):
            lon_grid, lat_grid = inverse.transform(x_grid, y_grid)
    else:
        raise ValueError(f"reproject_frame: unknown projection {projection!r}")

    with np.errstate(invalid="ignore"):
        src_col, src_row = _project_to_pixels(src_transformer, src_extent, lon_grid, lat_grid, src_w, src_h)

    valid = (
        np.isfinite(src_col) & np.isfinite(src_row)
        & (src_col >= 0) & (src_col < src_w) & (src_row >= 0) & (src_row < src_h)
    )
    col_idx = np.where(valid, src_col, 0).astype(np.intp)
    row_idx = np.where(valid, src_row, 0).astype(np.intp)

    src_array = np.asarray(img.convert("RGB"))
    out_array = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    out_array[valid] = src_array[row_idx[valid], col_idx[valid]]
    return Image.fromarray(out_array, mode="RGB")


def draw_graticule(
    img: Image.Image, satellite: str, step_deg: float, color: tuple[int, int, int], opacity: int,
    area: AreaInfo | None = None, sector: str = "CONUS",
) -> Image.Image:
    """Draw a lat/lon grid on img (must be the raw, untrimmed/uncropped frame — see
    the pipeline-order note on overlay_* config fields). Uses the real per-frame
    `area` (any sector) when given, e.g. from a satpy_raw fetch; otherwise falls back
    to the hand-calibrated `sector` lookup (see _GEOS_AREA_BY_SECTOR) keyed by
    `satellite`."""
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fill = (*color, opacity)
    line_width = max(1, round(w / _OVERLAY_REFERENCE_WIDTH_PX))  # a 1px line is invisible at 5000x3000+

    def project(lons: np.ndarray, lats: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        if area is not None:
            return lonlat_to_pixels_area(area, lons, lats, w, h)
        return lonlat_to_pixels(satellite, lons, lats, w, h, sector)

    def draw_run(cols: np.ndarray, rows: np.ndarray) -> None:
        run: list[tuple[float, float]] = []
        for c, r in zip(cols, rows):
            if 0 <= c <= w and 0 <= r <= h and np.isfinite(c) and np.isfinite(r):
                run.append((float(c), float(r)))
            else:
                if len(run) > 1:
                    draw.line(run, fill=fill, width=line_width)
                run = []
        if len(run) > 1:
            draw.line(run, fill=fill, width=line_width)

    lon_samples = np.arange(-180, 180.01, 0.5)
    lat_samples = np.arange(-85, 85.01, 0.5)
    for lat in np.arange(-80, 80.01, step_deg):
        result = project(lon_samples, np.full_like(lon_samples, lat))
        if result:
            draw_run(*result)
    for lon in np.arange(-180, 180.01, step_deg):
        result = project(np.full_like(lat_samples, lon), lat_samples)
        if result:
            draw_run(*result)

    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    return base.convert("RGB")


def draw_city_markers(
    img: Image.Image, satellite: str, cities: tuple[CityMarker, ...], cfg: Config,
    area: AreaInfo | None = None, sector: str = "CONUS",
) -> Image.Image:
    """Draw labeled markers at each city's projected pixel position. Same
    raw-frame-only requirement and area-lookup fallback as draw_graticule."""
    if not cities:
        return img
    w, h = img.size
    lons = np.array([c.lon for c in cities])
    lats = np.array([c.lat for c in cities])
    result = lonlat_to_pixels_area(area, lons, lats, w, h) if area is not None else lonlat_to_pixels(satellite, lons, lats, w, h, sector)
    if result is None:
        return img
    cols, rows = result

    scale = max(1.0, w / _OVERLAY_REFERENCE_WIDTH_PX)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(cfg.info_font_path, round(cfg.overlay_city_font_size * scale))
    except OSError:
        font = ImageFont.load_default()

    r = cfg.overlay_city_marker_radius * scale
    color = cfg.overlay_city_color
    for city, c, row in zip(cities, cols, rows):
        if not (0 <= c <= w and 0 <= row <= h and np.isfinite(c) and np.isfinite(row)):
            continue
        draw.ellipse((c - r, row - r, c + r, row + r), outline=color, width=max(1, round(2 * scale)))
        draw.text((c + r + 4, row), city.name, font=font, fill=color)
    return img


def fetch_shell_geojson(command: tuple[str, ...], timeout: float) -> dict[str, Any] | None:
    """Run an external command (argv list, no shell parsing) and parse its stdout as
    GeoJSON. Returns None (logged) on any failure -- a broken provider must not break
    the whole update cycle."""
    if not command:
        return None
    try:
        result = subprocess.run(list(command), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logging.warning("overlay_shell_command %s failed to run: %s", command, e)
        return None
    if result.returncode != 0:
        logging.warning(
            "overlay_shell_command %s exited %d: %s", command, result.returncode, result.stderr.strip(),
        )
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logging.warning("overlay_shell_command %s returned invalid JSON: %s", command, e)
        return None


def _iter_geojson_features(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a parsed GeoJSON payload (FeatureCollection, single Feature, or a
    bare geometry) to a flat list of Feature-shaped dicts."""
    gtype = geojson.get("type")
    if gtype == "FeatureCollection":
        return [f for f in geojson.get("features", []) if isinstance(f, dict)]
    if gtype == "Feature":
        return [geojson]
    if gtype:  # bare geometry, e.g. {"type": "Point", "coordinates": [...]}
        return [{"type": "Feature", "geometry": geojson, "properties": {}}]
    return []


def _resolve_feature_color(prop_color: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Resolve a feature's `properties.color` to an (r, g, b) tuple. Accepts an
    [r, g, b] list/tuple (the documented format) or a string -- either a hex code
    (`"#ff0000"`) or one of PIL's ~140 named colors (`"red"`), since that's what
    real-world GeoJSON tools (geojson.io, GitHub's simplestyle-spec) actually emit.
    Falls back to `default` (logged) for anything that doesn't parse, rather than
    raising and losing the whole overlay over one bad feature."""
    if not prop_color:
        return default
    if isinstance(prop_color, str):
        try:
            return ImageColor.getrgb(prop_color)[:3]
        except ValueError:
            logging.warning("Unrecognized properties.color %r; using default color", prop_color)
            return default
    try:
        return (int(prop_color[0]), int(prop_color[1]), int(prop_color[2]))
    except (TypeError, IndexError, ValueError):
        logging.warning("Unrecognized properties.color %r; using default color", prop_color)
        return default


def _draw_lonlat_run(
    draw: ImageDraw.ImageDraw, satellite: str, coords: list[list[float]], w: int, h: int,
    fill: tuple[int, ...], width: int, close: bool = False, sector: str = "CONUS",
) -> None:
    """Project a line/ring of [lon, lat] pairs and draw it, breaking the line
    wherever a point falls outside the frame (same run-breaking approach as
    draw_graticule's draw_run)."""
    if len(coords) < 2:
        return
    if close and coords[0] != coords[-1]:
        coords = [*coords, coords[0]]
    lons = np.array([c[0] for c in coords])
    lats = np.array([c[1] for c in coords])
    result = lonlat_to_pixels(satellite, lons, lats, w, h, sector)
    if result is None:
        return
    cols, rows = result
    run: list[tuple[float, float]] = []
    for c, r in zip(cols, rows):
        if 0 <= c <= w and 0 <= r <= h and np.isfinite(c) and np.isfinite(r):
            run.append((float(c), float(r)))
        else:
            if len(run) > 1:
                draw.line(run, fill=fill, width=width)
            run = []
    if len(run) > 1:
        draw.line(run, fill=fill, width=width)


def _build_geojson_layer(
    satellite: str, features: list[dict[str, Any]], w: int, h: int,
    color: tuple[int, int, int], line_width: int, marker_radius: int, opacity: int,
    font_path: str = "", font_size: int = 14, sector: str = "CONUS",
) -> Image.Image:
    """Project + draw Point/MultiPoint/LineString/MultiLineString/Polygon/
    MultiPolygon features onto a fresh (w, h) transparent RGBA layer. Per-feature
    `properties.color` (see _resolve_feature_color -- an [r, g, b] list, a hex string,
    or a named color) overrides the given default color; a Point/MultiPoint feature's
    `properties.name`, if present, is drawn as a text label next to its marker (same
    layout as draw_city_markers). Returns just the layer (not
    composited onto anything) so callers can cache it."""
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    scale = max(1.0, w / _OVERLAY_REFERENCE_WIDTH_PX)
    width = max(1, round(line_width * scale))
    radius = marker_radius * scale
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None  # loaded lazily, only if a label is actually drawn

    def draw_point(lon: float, lat: float, fill: tuple[int, ...], label: str | None) -> None:
        nonlocal font
        result = lonlat_to_pixels(satellite, np.array([lon]), np.array([lat]), w, h, sector)
        if result is None:
            return
        c, r = result[0][0], result[1][0]
        if not (0 <= c <= w and 0 <= r <= h and np.isfinite(c) and np.isfinite(r)):
            return
        draw.ellipse((c - radius, r - radius, c + radius, r + radius), outline=fill, width=width)
        if label:
            if font is None:
                try:
                    font = ImageFont.truetype(font_path, round(font_size * scale))
                except OSError:
                    font = ImageFont.load_default()
            draw.text((c + radius + 4, r), label, font=font, fill=fill)

    for feature in features:
        geometry = feature.get("geometry") or {}
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        if gtype is None or coords is None:
            continue
        props = feature.get("properties") or {}
        fill = (*_resolve_feature_color(props.get("color"), color), opacity)
        label = props.get("name")

        if gtype == "Point":
            draw_point(coords[0], coords[1], fill, label)
        elif gtype == "MultiPoint":
            for lon, lat in coords:
                draw_point(lon, lat, fill, label)
        elif gtype == "LineString":
            _draw_lonlat_run(draw, satellite, coords, w, h, fill, width, sector=sector)
        elif gtype == "MultiLineString":
            for line in coords:
                _draw_lonlat_run(draw, satellite, line, w, h, fill, width, sector=sector)
        elif gtype == "Polygon":
            for ring in coords:
                _draw_lonlat_run(draw, satellite, ring, w, h, fill, width, close=True, sector=sector)
        elif gtype == "MultiPolygon":
            for polygon in coords:
                for ring in polygon:
                    _draw_lonlat_run(draw, satellite, ring, w, h, fill, width, close=True, sector=sector)

    return overlay


def draw_geojson_overlay(
    img: Image.Image, satellite: str, geojson: dict[str, Any],
    color: tuple[int, int, int], line_width: int, marker_radius: int, opacity: int,
    font_path: str = "", font_size: int = 14, sector: str = "CONUS",
) -> Image.Image:
    """Draw whatever Point/MultiPoint/LineString/MultiLineString/Polygon/MultiPolygon
    features a GeoJSON payload contains, projected via lonlat_to_pixels. Per-feature
    `properties.color` ([r, g, b], a hex string, or a named color -- see
    _resolve_feature_color) overrides the plugin-level default color, and a
    Point/MultiPoint feature's `properties.name` is drawn as a text label."""
    features = _iter_geojson_features(geojson)
    if not features:
        return img
    w, h = img.size
    layer = _build_geojson_layer(satellite, features, w, h, color, line_width, marker_radius, opacity, font_path, font_size, sector)
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


def _geojson_files_cache_key(
    paths: tuple[str, ...], satellite: str, w: int, h: int,
    color: tuple[int, int, int], line_width: int, marker_radius: int, opacity: int,
    font_path: str, font_size: int, sector: str,
) -> dict[str, Any]:
    """Identifies exactly the inputs that affect the rendered layer -- if any of
    these change, the cached PNG is stale and must be rebuilt. mtime (not content
    hashing) is enough to detect an edited file cheaply."""
    file_stats = []
    for p in paths:
        try:
            mtime = Path(p).stat().st_mtime
        except OSError:
            mtime = None
        file_stats.append([p, mtime])
    return {
        "files": file_stats, "satellite": satellite, "sector": sector, "w": w, "h": h,
        "color": list(color), "line_width": line_width, "marker_radius": marker_radius, "opacity": opacity,
        "font_path": font_path, "font_size": font_size,
    }


def _geojson_files_cache_id(
    paths: tuple[str, ...], satellite: str, w: int, h: int,
    color: tuple[int, int, int], line_width: int, marker_radius: int, opacity: int,
    font_path: str, font_size: int, sector: str,
) -> str:
    """A short, stable identifier for one distinct (files, satellite, sector, frame
    size, style) combination, used to give each such combination its own cache file.
    Without this, every combo/satellite/resolution/sector sharing overlay_geojson_files
    would fight over one fixed filename: rendering combo B would invalidate and
    overwrite combo A's cached layer (their cache *keys* differ), so alternating
    between them in "rotate"/"per_monitor" mode would rebuild on every single cycle --
    never actually caching anything (see NEXT_STEPS.md item 16 for the related
    per-combo-overlay gap this compounds). Deliberately excludes each file's mtime --
    that still lives in the cache metadata and is checked separately, so editing a
    file invalidates the existing entry for this identity rather than minting a new
    cache file."""
    identity = {
        "paths": list(paths), "satellite": satellite, "sector": sector, "w": w, "h": h,
        "color": list(color), "line_width": line_width, "marker_radius": marker_radius,
        "opacity": opacity, "font_path": font_path, "font_size": font_size,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]


def render_static_geojson_overlay(img: Image.Image, cfg: Config, source: EffectiveSource) -> Image.Image:
    """Draw cfg.overlay_geojson_files onto img, caching the composited RGBA layer in
    cfg.data_dir. Unlike overlay_shell_command, these are static files that don't
    change cycle to cycle, so re-parsing and re-projecting every cycle is wasted work
    once a layer has any real size (e.g. full county borders) -- the cache key (each
    file's path/mtime + satellite/frame-size/style) means an unchanged config only
    pays that cost once, but editing a file or bumping resolution rebuilds it
    automatically. The cache *filename* is itself keyed on satellite/frame-size/style
    (_geojson_files_cache_id) so distinct combos each get their own cache entry
    instead of overwriting a shared one -- see its docstring."""
    if not cfg.overlay_geojson_files:
        return img
    w, h = img.size
    key = _geojson_files_cache_key(
        cfg.overlay_geojson_files, source.satellite, w, h,
        cfg.overlay_geojson_color, cfg.overlay_geojson_line_width,
        cfg.overlay_geojson_marker_radius, cfg.overlay_geojson_opacity,
        cfg.info_font_path, cfg.overlay_geojson_font_size, source.sector,
    )
    cache_id = _geojson_files_cache_id(
        cfg.overlay_geojson_files, source.satellite, w, h,
        cfg.overlay_geojson_color, cfg.overlay_geojson_line_width,
        cfg.overlay_geojson_marker_radius, cfg.overlay_geojson_opacity,
        cfg.info_font_path, cfg.overlay_geojson_font_size, source.sector,
    )
    cache_png = cfg.data_dir / f"overlay_geojson_cache_{cache_id}.png"
    cache_meta = cfg.data_dir / f"overlay_geojson_cache_{cache_id}.json"

    layer: Image.Image | None = None
    if cache_png.exists() and cache_meta.exists():
        try:
            if json.loads(cache_meta.read_text()) == key:
                layer = Image.open(cache_png).convert("RGBA")
        except (OSError, json.JSONDecodeError):
            layer = None

    if layer is None:
        features: list[dict[str, Any]] = []
        for path in cfg.overlay_geojson_files:
            try:
                geojson = json.loads(Path(path).read_text())
            except (OSError, json.JSONDecodeError) as e:
                logging.warning("overlay_geojson_files: couldn't read/parse %s: %s", path, e)
                continue
            features.extend(_iter_geojson_features(geojson))
        if not features:
            return img
        layer = _build_geojson_layer(
            source.satellite, features, w, h,
            cfg.overlay_geojson_color, cfg.overlay_geojson_line_width,
            cfg.overlay_geojson_marker_radius, cfg.overlay_geojson_opacity,
            cfg.info_font_path, cfg.overlay_geojson_font_size, source.sector,
        )
        try:
            cfg.data_dir.mkdir(parents=True, exist_ok=True)
            layer.save(cache_png)
            _atomic_write_text(cache_meta, json.dumps(key))
        except OSError as e:
            logging.warning("Couldn't write overlay_geojson_files cache: %s", e)

    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


def draw_overlays(img: Image.Image, cfg: Config, source: EffectiveSource, area: AreaInfo | None = None) -> Image.Image:
    """Apply configured georeferenced overlays. Must run on the raw fetched frame,
    before trim_source_caption/crop_fractional/crop_to_screen — those change the pixel
    grid the calibration above assumes.

    `area` is real per-frame georeferencing (only available for satpy_raw frames,
    see FetchedFrame.area_info) -- when given, it's valid for any sector, and the
    hand-calibrated/_GEOS_AREA_BY_SECTOR-allowlist gate below doesn't apply to
    overlay_graticule/overlay_cities (see draw_graticule/draw_city_markers, which
    both take `area` too). Without it (the cdn_jpg path, which has no georeferencing
    of its own), all four overlay kinds fall back to the hand-calibrated per-sector
    lookup (CONUS and Full Disk; Mesoscale has no fixed extent to hardcode) and its
    gate, keyed on `source.sector` -- see _GEOS_AREA_BY_SECTOR."""
    if not (cfg.overlay_graticule or cfg.overlay_cities or cfg.overlay_shell_command or cfg.overlay_geojson_files):
        return img
    if area is None:
        calibration = _GEOS_AREA_BY_SECTOR.get(source.sector)
        if calibration is None:
            logging.warning(
                "[%s] Georeferenced overlays are only calibrated for %s (sector=%s); skipping",
                source.name, "/".join(_GEOS_AREA_BY_SECTOR), source.sector,
            )
            return img
        if source.satellite not in calibration:
            logging.warning(
                "[%s] No %s overlay calibration for satellite=%s; skipping",
                source.name, source.sector, source.satellite,
            )
            return img

    if cfg.overlay_graticule:
        img = draw_graticule(img, source.satellite, cfg.overlay_graticule_step_deg, cfg.overlay_graticule_color, cfg.overlay_graticule_opacity, area, source.sector)
    if cfg.overlay_cities:
        img = draw_city_markers(img, source.satellite, cfg.overlay_cities, cfg, area, source.sector)
    if cfg.overlay_geojson_files:
        try:
            img = render_static_geojson_overlay(img, cfg, source)
        except Exception:
            logging.exception(
                "[%s] overlay_geojson_files overlay failed; skipping", source.name,
            )
    if cfg.overlay_shell_command:
        geojson = fetch_shell_geojson(cfg.overlay_shell_command, cfg.overlay_shell_timeout)
        if geojson is not None:
            try:
                img = draw_geojson_overlay(
                    img, source.satellite, geojson,
                    cfg.overlay_shell_color, cfg.overlay_shell_line_width,
                    cfg.overlay_shell_marker_radius, cfg.overlay_shell_opacity,
                    cfg.info_font_path, cfg.overlay_shell_font_size, source.sector,
                )
            except Exception:
                logging.exception(
                    "[%s] overlay_shell_command returned unusable GeoJSON; skipping", source.name,
                )
    return img


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class FetchedFrame:
    """One fetched-and-decoded source frame, in the common shape both source_kinds
    produce so the rest of the pipeline (build_metadata onward) doesn't need to know
    which one fetched it. `extra_meta` carries kind-specific fields: http_etag/
    http_last_modified/http_content_length for cdn_jpg, band_files for satpy_raw."""
    image: Image.Image
    capture_time_utc: str | None
    source_kind: str
    area_info: AreaInfo | None = None
    extra_meta: dict[str, Any] = field(default_factory=dict)


def build_metadata(source: EffectiveSource, frame: FetchedFrame) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    meta = {
        "combo": source.name,
        "satellite": source.satellite,
        "satellite_label": source.satellite_label(),
        "sector": source.sector,
        "sector_label": source.sector_label(),
        "product": source.product if frame.source_kind == "cdn_jpg" else "GeoColor (satpy_raw)",
        "resolution_requested": source.resolution if frame.source_kind == "cdn_jpg" else "native",
        "downloaded_at_utc": now.isoformat(),
        "capture_time_utc": frame.capture_time_utc,
        "image_dimensions": list(frame.image.size),
        "image_format": frame.image.format,
    }
    if frame.source_kind == "cdn_jpg":
        meta["source_url"] = source.image_url
        meta["http_last_modified"] = frame.extra_meta.get("last-modified")
        meta["http_etag"] = frame.extra_meta.get("etag")
        meta["http_content_length"] = frame.extra_meta.get("content-length")
    else:
        meta["source_url"] = "s3://" + ", ".join(frame.extra_meta.get("band_files", []))
        meta["download_bytes"] = frame.extra_meta.get("total_bytes")
    return meta


def embed_exif(img: Image.Image, meta: dict[str, Any]) -> Image.Image:
    """Bake key metadata into standard JPEG EXIF tags so it travels with the file."""
    exif = img.getexif()
    ImageDescription, Software, DateTimeOriginal, Artist = 0x010E, 0x0131, 0x9003, 0x013B

    description = (
        f"{meta['satellite_label']} {meta['sector_label']} {meta['product']} "
        f"captured {meta['capture_time_utc'] or 'unknown'}"
    )
    exif[ImageDescription] = description
    exif[Software] = "goes_wallpaper"
    exif[Artist] = "NOAA STAR / NESDIS"
    if meta["capture_time_utc"]:
        try:
            dt = datetime.fromisoformat(meta["capture_time_utc"])
            exif[DateTimeOriginal] = dt.strftime("%Y:%m:%d %H:%M:%S")
        except ValueError:
            pass

    img.info["exif"] = exif.tobytes()
    return img


# Floor on the info bar's pixel height regardless of info_block_height_frac, so text
# stays legible at very low resolutions/aggressive crops instead of shrinking to
# nothing. Not a measurement — just the smallest bar that still fits an ~12px font
# with padding (see font_size/pad below, which derive from bar_height).
_INFO_BAR_MIN_HEIGHT_PX = 28

# Floor for the shrink-to-fit loop below -- stop shrinking once the font would become
# illegible rather than chasing an exact fit for pathologically long text.
_INFO_BAR_MIN_FONT_PX = 10


def _fit_info_bar_font(draw: ImageDraw.ImageDraw, left_text: str, right_text: str, font_path: str, font_size: int, available_width: int, pad: int) -> ImageFont.FreeTypeFont:
    """Pick the largest font size (down to _INFO_BAR_MIN_FONT_PX) at which left_text and
    right_text, drawn left- and right-aligned with `pad` on each end and between them,
    don't overlap. A long product label (e.g. satpy_raw's "GeoColor (satpy_raw)") on a
    square Full Disk frame can outrun the bar width at the nominal size otherwise."""
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        return ImageFont.load_default()

    while font_size > _INFO_BAR_MIN_FONT_PX and draw.textlength(left_text, font=font) + draw.textlength(right_text, font=font) + 3 * pad > available_width:
        font_size = round(font_size * 0.9)
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            return ImageFont.load_default()
    return font


def draw_info_block(img: Image.Image, cfg: Config, meta: dict[str, Any], platform: WallpaperPlatform) -> Image.Image:
    img = img.convert("RGB")
    width, height = img.size
    bar_height = max(_INFO_BAR_MIN_HEIGHT_PX, round(height * cfg.info_block_height_frac))
    bottom_margin = platform.get_taskbar_height() if cfg.avoid_taskbar else 0

    overlay = Image.new("RGBA", (width, bar_height), (0, 0, 0, cfg.info_block_opacity))
    draw = ImageDraw.Draw(overlay)

    capture_local = "unknown"
    if meta["capture_time_utc"]:
        dt_utc = datetime.fromisoformat(meta["capture_time_utc"])
        capture_local = dt_utc.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    left_text = f"{meta['satellite_label']}  •  {meta['sector_label']}  •  {meta['product']}"
    right_text = f"Captured {capture_local}"

    pad = round(bar_height * 0.25)
    font_size = max(12, round(bar_height * 0.42))
    font = _fit_info_bar_font(draw, left_text, right_text, cfg.info_font_path, font_size, width, pad)

    draw.text((pad, bar_height // 2), left_text, font=font, fill=(255, 255, 255, 255), anchor="lm")
    draw.text((width - pad, bar_height // 2), right_text, font=font, fill=(255, 255, 255, 255), anchor="rm")

    base = img.convert("RGBA")
    base.alpha_composite(overlay, (0, height - bar_height - bottom_margin))
    return base.convert("RGB")



# --------------------------------------------------------------------------- #
# Capture-time-aware scheduling
# --------------------------------------------------------------------------- #

def update_capture_phase(cfg: Config, state: dict[str, Any], capture_time_iso: str) -> None:
    """Track *when within each interval* fresh frames tend to actually post (e.g. ~40s
    after each 5-minute boundary), so the loop can wake up shortly after that instead
    of guessing at the raw clock boundary. Uses a circular EMA since the phase wraps
    around at the interval boundary (e.g. 359s and 2s are only 3s apart, not 357s)."""
    interval = cfg.interval_minutes * 60
    phase = datetime.fromisoformat(capture_time_iso).timestamp() % interval

    prior = state.get("capture_phase_seconds")
    prior_interval = state.get("capture_phase_interval_minutes")
    if prior is None or prior_interval != cfg.interval_minutes:
        new_phase = phase
    else:
        diff = ((phase - prior + interval / 2) % interval) - interval / 2
        new_phase = (prior + 0.3 * diff) % interval

    state["capture_phase_seconds"] = new_phase
    state["capture_phase_interval_minutes"] = cfg.interval_minutes


def compute_next_run(cfg: Config, state: dict[str, Any], now: float) -> float:
    interval = cfg.interval_minutes * 60
    if not cfg.align_to_clock:
        return now + interval

    phase = state.get("capture_phase_seconds")
    if not cfg.sync_to_capture_time or phase is None or state.get("capture_phase_interval_minutes") != cfg.interval_minutes:
        return (now // interval + 1) * interval

    next_run = (now // interval) * interval + phase + cfg.capture_offset_buffer_seconds
    while next_run <= now:
        next_run += interval
    return next_run


def maybe_wait_for_sync(cfg: Config, state: dict[str, Any], source: EffectiveSource) -> None:
    """See wait_for_sync_time's docstring on Config: sleep once (this cycle, this
    source) until shortly after the next frame should land, rather than fetching
    immediately. A no-op if disabled, if this source's phase hasn't been learned yet,
    or if the target has already passed."""
    if not cfg.wait_for_sync_time:
        return
    sstate = state.get("sources", {}).get(source.key, {})
    phase = sstate.get("capture_phase_seconds")
    if phase is None or sstate.get("capture_phase_interval_minutes") != cfg.interval_minutes:
        return

    interval = cfg.interval_minutes * 60
    now = time.time()
    target = (now // interval) * interval + phase + cfg.capture_offset_buffer_seconds
    wait = target - now
    if wait <= 0:
        return
    if wait > cfg.wait_for_sync_max_seconds:
        logging.info(
            "[%s] Computed presync wait %.0fs exceeds wait_for_sync_max_seconds (%.0fs); fetching now instead",
            source.name, wait, cfg.wait_for_sync_max_seconds,
        )
        return

    logging.info("[%s] Waiting %.0fs for the next frame's likely publish time before fetching", source.name, wait)
    time.sleep(wait)


# --------------------------------------------------------------------------- #
# Power/network-aware fallbacks
# --------------------------------------------------------------------------- #

def should_skip_for_power(cfg: Config, platform: WallpaperPlatform) -> bool:
    """See skip_on_battery's docstring on Config. Unknown battery state (can't be
    detected on this platform/hardware) is treated as "not on battery" — never skips
    on a guess."""
    if not cfg.skip_on_battery:
        return False
    power = platform.get_power_state()
    if power.on_battery:
        logging.info(
            "Skipping cycle: running on battery power (skip_on_battery=true, %s%% remaining)",
            power.battery_percent if power.battery_percent is not None else "unknown",
        )
        return True
    return False


def maybe_apply_metered_resolution(cfg: Config, source: EffectiveSource, platform: WallpaperPlatform) -> EffectiveSource:
    """See metered_resolution's docstring on Config. Unknown network-cost state
    (can't be detected on this platform/hardware) is treated as "not metered" — never
    downgrades resolution on a guess."""
    if not cfg.metered_resolution or cfg.metered_resolution == source.resolution:
        return source
    if platform.is_network_metered():
        logging.info(
            "[%s] Network is metered; using %s instead of %s this cycle",
            source.name, cfg.metered_resolution, source.resolution,
        )
        return replace(source, resolution=cfg.metered_resolution)
    return source


# --------------------------------------------------------------------------- #
# Core run
# --------------------------------------------------------------------------- #

def _fetch_cdn_jpg(
    cfg: Config, session: requests.Session, source: EffectiveSource, sstate: dict[str, Any],
) -> FetchedFrame | None:
    """Today's default source_kind: NOAA STAR's pre-rendered JPG over HTTP."""
    prev_etag = sstate.get("etag") if cfg.skip_if_unchanged else None
    prev_capture_time = sstate.get("last_capture_time_utc")

    started = time.monotonic()
    result = fetch_fresh_image(
        cfg, session, source.image_url, prev_etag, prev_capture_time,
        started + cfg.max_fresh_wait_seconds,
    )
    elapsed = time.monotonic() - started
    if result is None:
        logging.info("[%s] No new image available", source.name)
        return None

    content, headers = result
    logging.info("[%s] Downloaded %d bytes in %.2fs", source.name, len(content), elapsed)

    img = Image.open(io.BytesIO(content))
    img.load()
    return FetchedFrame(
        image=img,
        capture_time_utc=parse_capture_time(headers),
        source_kind="cdn_jpg",
        extra_meta=headers,
    )


def _fetch_satpy_raw(cfg: Config, source: EffectiveSource, sstate: dict[str, Any]) -> FetchedFrame | None:
    """source_kind = "satpy_raw": composite our own GeoColor from raw ABI L1b bands
    (see source_satpy.py). Lazily imports source_satpy so the heavy satpy/pyresample/
    s3fs dependencies are only required when this source_kind is actually used."""
    import source_satpy

    prev_scan_time = sstate.get("last_capture_time_utc") if cfg.skip_if_unchanged else None
    started = time.monotonic()
    try:
        result = source_satpy.fetch_composite(
            source.satellite, source.sector, prev_scan_time, cfg.data_dir / "satpy_raw_cache",
        )
    except source_satpy.SatpyUnavailableError as e:
        logging.error("[%s] %s", source.name, e)
        return None
    elapsed = time.monotonic() - started
    if result is None:
        logging.info("[%s] No new satpy_raw composite available", source.name)
        return None

    logging.info(
        "[%s] Composited satpy_raw frame in %.2fs (%d band files, %d bytes downloaded)",
        source.name, elapsed, len(result.band_files), result.total_bytes,
    )
    return FetchedFrame(
        image=result.image,
        capture_time_utc=result.scan_time_utc,
        source_kind="satpy_raw",
        area_info=AreaInfo(proj4_params=result.proj4_params, extent=result.extent),
        extra_meta={"band_files": result.band_files, "total_bytes": result.total_bytes},
    )


def fetch_and_render(
    cfg: Config,
    session: requests.Session,
    source: EffectiveSource,
    state: dict[str, Any],
    screen_size: tuple[int, int],
    platform: WallpaperPlatform,
) -> tuple[Image.Image, dict[str, Any]] | None:
    """Fetch (with freshness retry), decode, and fully render one EffectiveSource's
    frame into a screen_size-sized final image + its metadata. Updates state in place
    (per-source ETag/capture-time/learned publish phase, keyed by source.key so
    unrelated sources sharing one config never mix up each other's freshness
    tracking). Returns None if the source genuinely hasn't changed (a cdn_jpg 304, or
    the satpy_raw equivalent -- same scan time as last cycle)."""
    source = maybe_apply_metered_resolution(cfg, source, platform)
    sstate = state.setdefault("sources", {}).setdefault(source.key, {})
    prev_capture_time = sstate.get("last_capture_time_utc")

    if source.source_kind == "cdn_jpg":
        frame = _fetch_cdn_jpg(cfg, session, source, sstate)
    elif source.source_kind == "satpy_raw":
        frame = _fetch_satpy_raw(cfg, source, sstate)
    else:
        raise ValueError(f"Unknown source_kind: {source.source_kind!r}")
    if frame is None:
        return None

    with frame.image as img:
        meta = build_metadata(source, frame)

        img = draw_overlays(img, cfg, source, frame.area_info)

        # NOAA's baked caption strip is a cdn_jpg-only artifact -- a satpy_raw
        # composite never has one to trim.
        if source.source_kind == "cdn_jpg" and cfg.trim_source_caption:
            img = trim_source_caption(img, cfg.trim_source_caption_frac)

        did_reproject = False
        if cfg.output_projection != "native":
            center_lon = cfg.output_projection_center_lon
            if center_lon is None:
                center_lon = _satellite_lon_0(source.satellite, source.sector, frame.area_info) or 0.0
            center_lat = cfg.output_projection_center_lat if cfg.output_projection_center_lat is not None else 0.0
            out_w = img.width
            if cfg.output_projection in _BOUNDS_FRAMED_PROJECTIONS:
                bounds = (source.crop_min_lon, source.crop_min_lat, source.crop_max_lon, source.crop_max_lat)
                # A degrees-based aspect ratio approximation -- exact for platecarree,
                # only approximate for lambertconformal's actual projected aspect, but
                # close enough for a CONUS-sized box, and crop_to_screen's cover-crop
                # corrects the final aspect to the real screen anyway.
                lon_span, lat_span = bounds[2] - bounds[0], bounds[3] - bounds[1]
                out_h = max(1, round(out_w * lat_span / lon_span)) if lon_span else img.height
            else:  # "orthographic"/"lambertazimuthal" -- square canvas, globe isn't an ellipse
                bounds = None
                out_h = out_w

            reprojected = reproject_frame(
                img, source.satellite, source.sector, frame.area_info,
                cfg.output_projection, bounds, center_lon, center_lat, out_w, out_h,
                cfg.output_projection_lcc_lat1, cfg.output_projection_lcc_lat2,
            )
            if reprojected is not None:
                img = reprojected
                did_reproject = True
            else:
                logging.warning(
                    "[%s] No calibration for satellite=%s sector=%s; output_projection skipped, "
                    "using the native projection", source.name, source.satellite, source.sector,
                )

        if not did_reproject:
            # The reprojection's own bounds/framing already is the crop when it ran --
            # only apply the region-of-interest crop on top of the native projection.
            crop_box = (source.crop_left, source.crop_top, source.crop_right, source.crop_bottom)
            if source.crop_min_lon is not None:
                lonlat_box = lonlat_box_to_crop_fraction(
                    source.satellite, source.sector, frame.area_info, *img.size,
                    source.crop_min_lon, source.crop_min_lat, source.crop_max_lon, source.crop_max_lat,
                )
                if lonlat_box is not None:
                    crop_box = lonlat_box
                else:
                    logging.warning(
                        "[%s] No calibration for satellite=%s sector=%s; falling back to the fractional crop",
                        source.name, source.satellite, source.sector,
                    )
            img = crop_fractional(img, *crop_box)

        if cfg.crop_to_screen:
            img = crop_to_screen(img, screen_size, cfg.crop_anchor)
            meta["screen_size"] = list(screen_size)
            logging.info("[%s] Cropped to %s", source.name, screen_size)

        if cfg.info_block:
            img = draw_info_block(img, cfg, meta, platform)

        img = embed_exif(img, meta)

    if source.source_kind == "cdn_jpg":
        sstate["etag"] = frame.extra_meta.get("etag")
    if meta["capture_time_utc"]:
        if meta["capture_time_utc"] != prev_capture_time:
            update_capture_phase(cfg, sstate, meta["capture_time_utc"])
        sstate["last_capture_time_utc"] = meta["capture_time_utc"]

    return img, meta


def _write_render_to(path: Path, img: Image.Image, label: str = "") -> None:
    """See Config.render_to's docstring: save a rendered frame for inspection
    without applying it as the wallpaper."""
    path.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"exif": img.info.get("exif", b"")} if path.suffix.lower() in (".jpg", ".jpeg") else {}
    img.save(path, **save_kwargs)
    prefix = f"[{label}] " if label else ""
    logging.info("%sRendered frame saved to %s (--render-to set, wallpaper not applied)", prefix, path)


def run_once(cfg: Config, session: requests.Session, platform: WallpaperPlatform) -> bool:
    """combo_mode = "single": fetch-crop-annotate-apply the one top-level configured
    source. Returns True if the wallpaper changed."""
    if should_skip_for_power(cfg, platform):
        return False

    state = load_state(cfg)
    source = resolve_source(cfg, None)
    state["last_source_key"] = source.key
    maybe_wait_for_sync(cfg, state, source)

    screen_size = platform.get_screen_size(cfg.span_all_monitors, cfg.screen_width, cfg.screen_height, cfg.wmi_screen_size_fallback) if cfg.crop_to_screen else (0, 0)
    result = fetch_and_render(cfg, session, source, state, screen_size, platform)
    if result is None:
        logging.info("Leaving current wallpaper in place")
        save_state(cfg, state)
        return False
    img, meta = result

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    img.save(cfg.wallpaper_path, "JPEG", quality=92, exif=img.info.get("exif", b""))
    _atomic_write_text(cfg.metadata_path, json.dumps(meta, indent=2))
    logging.info("Saved wallpaper + metadata to %s", cfg.data_dir)

    if cfg.render_to:
        _write_render_to(cfg.render_to, img)
    else:
        platform.apply_wallpaper(cfg.wallpaper_path, cfg.wallpaper_style)
        logging.info("Wallpaper applied (style=%s)", cfg.wallpaper_style)

    state["last_applied_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(cfg, state)
    return True


def run_once_rotate(cfg: Config, session: requests.Session, platform: WallpaperPlatform) -> bool:
    """combo_mode = "rotate": cycle through cfg.combos one per cycle (index persisted
    in state.json), applied as a single wallpaper just like "single" mode."""
    if not cfg.combos:
        raise ValueError('combo_mode = "rotate" requires at least one [[combos]] entry')
    if should_skip_for_power(cfg, platform):
        return False

    state = load_state(cfg)
    index = state.get("combo_rotation_index", 0) % len(cfg.combos)
    combo = cfg.combos[index]
    source = resolve_source(cfg, combo)
    state["last_source_key"] = source.key
    maybe_wait_for_sync(cfg, state, source)

    screen_size = platform.get_screen_size(cfg.span_all_monitors, cfg.screen_width, cfg.screen_height, cfg.wmi_screen_size_fallback) if cfg.crop_to_screen else (0, 0)
    result = fetch_and_render(cfg, session, source, state, screen_size, platform)
    if result is None:
        logging.info("[%s] Leaving current wallpaper in place", combo.name)
        save_state(cfg, state)
        return False
    img, meta = result

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    img.save(cfg.wallpaper_path, "JPEG", quality=92, exif=img.info.get("exif", b""))
    _atomic_write_text(cfg.metadata_path, json.dumps(meta, indent=2))
    logging.info("[%s] Saved wallpaper + metadata to %s", combo.name, cfg.data_dir)

    if cfg.render_to:
        _write_render_to(cfg.render_to, img, combo.name)
    else:
        platform.apply_wallpaper(cfg.wallpaper_path, cfg.wallpaper_style)
        logging.info("[%s] Wallpaper applied (style=%s)", combo.name, cfg.wallpaper_style)

    state["combo_rotation_index"] = (index + 1) % len(cfg.combos)
    state["last_applied_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(cfg, state)
    return True


def run_once_per_monitor(cfg: Config, session: requests.Session, platform: WallpaperPlatform) -> bool:
    """combo_mode = "per_monitor": each combo's `monitor` index gets its own
    independently rendered+applied wallpaper. Monitors with no assigned combo are
    left untouched. Returns True if any monitor was updated."""
    if not cfg.combos:
        raise ValueError('combo_mode = "per_monitor" requires at least one [[combos]] entry')
    if should_skip_for_power(cfg, platform):
        return False

    state = load_state(cfg)
    by_monitor = {combo.monitor: combo for combo in cfg.combos if combo.monitor is not None}

    monitors = platform.list_monitors()
    logging.info("Found %d active monitor(s)", len(monitors))

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    assignments: dict[str, Path] = {}
    for i, monitor in enumerate(monitors):
        combo = by_monitor.get(i)
        if combo is None:
            logging.info("Monitor %d has no assigned combo; leaving it untouched", i)
            continue

        source = resolve_source(cfg, combo)
        result = fetch_and_render(cfg, session, source, state, (monitor.width, monitor.height), platform)
        if result is None:
            logging.info("[%s] Leaving monitor %d's wallpaper in place", combo.name, i)
            continue
        img, meta = result

        out_path = cfg.data_dir / f"wallpaper_monitor{i}.jpg"
        img.save(out_path, "JPEG", quality=92, exif=img.info.get("exif", b""))
        _atomic_write_text(cfg.data_dir / f"wallpaper_monitor{i}.json", json.dumps(meta, indent=2))
        assignments[monitor.id] = out_path
        logging.info("[%s] Rendered for monitor %d (%s)", combo.name, i, monitor.id)

        if cfg.render_to:
            render_path = cfg.render_to.with_stem(f"{cfg.render_to.stem}_monitor{i}")
            _write_render_to(render_path, img, combo.name)

    if assignments and not cfg.render_to:
        platform.apply_wallpaper_per_monitor(assignments, cfg.wallpaper_style)
        for i, monitor in enumerate(monitors):
            if monitor.id in assignments:
                logging.info("Applied to monitor %d (%s)", i, monitor.id)
        state["last_applied_utc"] = datetime.now(timezone.utc).isoformat()

    save_state(cfg, state)
    return bool(assignments)


_CYCLE_FUNCS = {
    "single": run_once,
    "rotate": run_once_rotate,
    "per_monitor": run_once_per_monitor,
}


def run_loop(cfg: Config, session: requests.Session, platform: WallpaperPlatform) -> None:
    """Run indefinitely, waking on drift-corrected boundaries instead of naive sleep(),
    so the effective cadence doesn't creep as each cycle's own runtime accumulates.
    When sync_to_capture_time is enabled, the boundary itself is nudged to line up
    with when fresh frames actually post rather than the raw clock tick — driven by
    whichever source state["last_source_key"] points at (unset in "per_monitor" mode,
    since multiple sources are fetched per cycle there; falls back to plain
    clock-boundary alignment in that case)."""
    cycle = _CYCLE_FUNCS[cfg.combo_mode]
    while True:
        try:
            cycle(cfg, session, platform)
        except Exception:
            logging.exception("Cycle failed; will retry next interval")

        state = load_state(cfg)
        now = time.time()
        key = state.get("last_source_key")
        sstate = state.get("sources", {}).get(key, {}) if key else {}
        next_run = compute_next_run(cfg, sstate, now)
        next_run += cfg.jitter_seconds * (0.5 - _rand_unit())

        sleep_for = max(1.0, next_run - time.time())
        logging.info("Sleeping %.1fs until next cycle", sleep_for)
        time.sleep(sleep_for)


def _rand_unit() -> float:
    return random.random()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to a TOML config file")
    p.add_argument("--satellite", help="e.g. GOES19, GOES18")
    p.add_argument("--sector", help="e.g. CONUS, FD, M1, M2")
    p.add_argument("--product", help="e.g. GEOCOLOR")
    p.add_argument("--resolution", help='e.g. "2500x1500" or "latest"')
    p.add_argument("--data-dir", type=Path, dest="data_dir")
    p.add_argument(
        "--render-to", type=Path, dest="render_to",
        help="Also save the rendered frame(s) to this path and skip applying them as "
             "the desktop wallpaper (for testing a render without touching the real "
             "wallpaper). With combo_mode = \"per_monitor\", writes one file per "
             "monitor with `_monitor{i}` inserted before the extension.",
    )
    p.add_argument("--wallpaper-style", choices=list(WALLPAPER_STYLE_NAMES), dest="wallpaper_style")
    p.add_argument("--no-crop", action="store_const", const=False, dest="crop_to_screen")
    p.add_argument("--no-info-block", action="store_const", const=False, dest="info_block")
    p.add_argument("--span-all-monitors", action="store_const", const=True, dest="span_all_monitors")
    p.add_argument("--loop", action="store_const", const=True, dest="loop")
    p.add_argument(
        "--wait-for-sync", action="store_const", const=True, dest="wait_for_sync_time",
        help="Single-shot/Task Scheduler use: sleep until shortly after the next frame's "
             "learned publish time before fetching, instead of fetching immediately.",
    )
    p.add_argument("--interval-minutes", type=int, dest="interval_minutes")
    p.add_argument("--log-level", dest="log_level")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    overrides = {
        k: v for k, v in vars(args).items() if k != "config"
    }
    # Chicken-and-egg: get_platform() needs cfg.platform (which backend to force, if
    # any) before it can run, but load_config() wants a WallpaperPlatform in hand to
    # supply data_dir/info_font_path defaults. Resolve it with a cheap first pass
    # that only needs cfg.platform -- load_config() itself is a pure TOML/overrides
    # read, safe to call twice.
    platform_probe_cfg = load_config(args.config, overrides)
    validate_platform(platform_probe_cfg)
    platform = get_platform(platform_probe_cfg.platform)
    cfg = load_config(args.config, overrides, platform=platform)
    validate_combos(cfg)
    validate_source_kind(cfg)
    validate_lonlat_crop_bounds(cfg)
    validate_output_projection(cfg)
    validate_platform(cfg)
    setup_logging(cfg)

    session = build_session(cfg)
    try:
        if cfg.loop:
            run_loop(cfg, session, platform)
        else:
            _CYCLE_FUNCS[cfg.combo_mode](cfg, session, platform)
    except KeyboardInterrupt:
        logging.info("Interrupted, exiting")
        return 130
    except Exception:
        logging.exception("Unhandled exception while running goes_wallpaper")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
