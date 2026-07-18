# source_satpy.py -- opt-in raw-ABI-band GeoColor compositing (source_kind = "satpy_raw")
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

"""Alternative to goes_wallpaper.py's default CDN-JPG fetch: pulls raw ABI L1b
radiance bands from the public noaa-goes16/18/19 S3 buckets (anonymous access) and
composites our own GeoColor image with satpy, instead of fetching NOAA STAR's
already-rendered JPG. See CUSTOM_IMAGERY_PLAN.md for the full rationale (removes
NOAA's baked-in state lines/logo, and exposes real georeferencing for any sector --
not just the two CONUS extents goes_wallpaper.py's _GEOS_AREA_CONUS hand-calibrates).

satpy/pyresample/xarray/dask/trollimage/s3fs are an optional install extra
(`pip install goes-wallpaper[satpy-raw]`) -- this module must stay importable
without them installed, so every one of those imports is deferred into the
function that actually needs it, never at module load time. Only call
check_available()/find_latest_scan_time()/fetch_composite() from a context that's
prepared to handle SatpyUnavailableError.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image

# Our composite's fixed input bands: C01 (blue) + C02 (red, highest native
# resolution) + C03 (veggie/NIR, used to synthesize green) for the daytime
# true_color layer; C13 (clean longwave IR window, day/night) for the muted-color
# night layer (see _muted_ir_night_rgb). Not user-configurable in v1 -- see
# NEXT_STEPS-style non-goal list in CUSTOM_IMAGERY_PLAN.md's phasing notes. (C07,
# shortwave IR, was considered for a fog/low-cloud texture boost on the night side
# but left out of v1 -- its brightness-temperature-difference signature needs
# validating against a real nighttime scan before trusting the sign/scale, which
# this session's live testing happened to run during CONUS daylight hours.)
_REQUIRED_BANDS = {1, 2, 3, 13}

# Compositing at each band's true native resolution and letting satpy's "native"
# resampler upsample everything to match Band 2 (the finest) is expensive enough to
# matter: a live CONUS composite came out at 10000x6000 (~55-70s to compute) and a
# live Full Disk composite (whose native grid is considerably larger still) didn't
# finish compositing within a 5-minute budget, even though the ~550MB download
# itself completed quickly. The output gets downsized to screen resolution by
# crop_to_screen anyway, so compositing at full native resolution first is wasted
# work. Resampling down to this target width (aspect-ratio-preserved from whatever
# the sector's native shape is) before compositing cuts pixel count -- and Rayleigh
# correction compute, which scales with it -- substantially, especially for Full
# Disk. 5000px matches Config.resolution's own default/documented rationale (comfortably
# covers a 4K/3840x2160 monitor after a full-frame crop) for consistency with the
# cdn_jpg path, not a re-derivation for this path specifically.
#
# TODO: derive this from the actual configured screen size (with some headroom
# factor) instead of a fixed constant, the same way cdn_jpg's `resolution` tier
# selection could in principle be screen-aware -- would need threading screen_size
# (currently only known in goes_wallpaper.py's fetch_and_render) down into
# fetch_composite, and deciding how much headroom to keep for source_crop_*/combo
# crop_* users who intentionally crop into a sub-region before the screen-fit crop.
_COMPOSITE_TARGET_WIDTH_PX = 5000

# Solar zenith angle blend thresholds (degrees) for the day/night terminator,
# matching satpy's own geo_color composite's lim_low/lim_high (etc/composites/
# abi.yaml) so the transition softness matches NOAA's GEOCOLOR convention.
_NIGHT_BLEND_LIM_LOW_DEG = 78.0
_NIGHT_BLEND_LIM_HIGH_DEG = 88.0

# Muted "moonlit" night palette: cold cloud tops render pale, warm clear surface
# renders dark navy -- a deliberately desaturated, photographic feel instead of the
# vivid rainbow palettes common in IR weather graphics. Tuned by eye against a
# synthetic brightness-temperature gradient (see tests/test_source_satpy.py), not
# derived from a physical calibration -- adjust freely if it looks wrong on real data.
_NIGHT_WARM_RGB = (18, 22, 40)
_NIGHT_COLD_RGB = (215, 220, 238)
_NIGHT_BT_WARM_K = 295.0  # brightness temp mapped to the fully "warm" (dark) end
_NIGHT_BT_COLD_K = 200.0  # brightness temp mapped to the fully "cold" (pale) end

_SATELLITE_TO_BUCKET = {
    "GOES16": "noaa-goes16",
    "GOES18": "noaa-goes18",
    "GOES19": "noaa-goes19",
}

# NOAA STAR's sector naming (used elsewhere in goes_wallpaper.py's CDN URLs) vs. the
# AWS Open Data ABI L1b bucket's own folder/filename conventions -- these are two
# independently-invented naming schemes for the same four sectors, not the same
# string reused. AWS folder-per-sector EXCEPT mesoscale, which shares one
# "ABI-L1b-RadM" folder for both M1 and M2 (the M1/M2 distinction only exists in
# each object's filename, e.g. "...-RadM1-...", not the folder path) -- verify this
# against a live `s3fs`/`aws s3 ls` listing before relying on it, this was derived
# from documented AWS conventions, not confirmed against a real listing in this pass.
_SECTOR_TO_S3_FOLDER = {
    "CONUS": "ABI-L1b-RadC",
    "FD": "ABI-L1b-RadF",
    "M1": "ABI-L1b-RadM",
    "M2": "ABI-L1b-RadM",
}
_SECTOR_TO_S3_FILE_TOKEN = {
    "CONUS": "RadC",
    "FD": "RadF",
    "M1": "RadM1",
    "M2": "RadM2",
}

# Matches "...C02_G18_s20241601801173_e...", capturing the band number and the
# 14-digit scan-start token (sYYYYDDDHHMMSSt, tenths-of-a-second precision).
_BAND_AND_SCAN_RE = re.compile(r"C(\d{2})_G\d+_s(\d{14})")


class SatpyUnavailableError(RuntimeError):
    """satpy/pyresample/s3fs aren't importable. Raised instead of letting a raw
    ImportError surface, so callers get an actionable message."""


@dataclass(slots=True)
class RawFetchResult:
    """One composited GeoColor frame, ready to feed into goes_wallpaper.py's
    existing crop/info-block/EXIF pipeline the same way a decoded CDN JPG is."""
    image: Image.Image
    scan_time_utc: str                            # ISO8601, this scan's actual start time
    extent: tuple[float, float, float, float]      # AreaDefinition.area_extent (x0, y0, x1, y1), meters
    proj4_params: dict[str, Any]                   # AreaDefinition.crs.to_dict()
    band_files: list[str]                          # S3 keys used, for logging/metadata
    total_bytes: int                               # sum of downloaded band file sizes, for logging/metadata


@dataclass(slots=True)
class _ScanSelection:
    scan_time_utc: str
    scan_time_token: str
    keys: dict[int, str]  # band number -> S3 key


def check_available() -> None:
    """Raise SatpyUnavailableError with an actionable message if the satpy-raw
    extra isn't installed. Call this before any network work, so failures surface
    immediately rather than partway through a fetch."""
    try:
        import satpy  # noqa: F401
        import pyresample  # noqa: F401
        import s3fs  # noqa: F401
    except ImportError as e:
        raise SatpyUnavailableError(
            'source_kind = "satpy_raw" requires the optional satpy-raw dependencies. '
            "Install with: pip install goes-wallpaper[satpy-raw]  (or: uv sync --extra satpy-raw)"
        ) from e


def _bucket_for_satellite(satellite: str) -> str:
    try:
        return _SATELLITE_TO_BUCKET[satellite]
    except KeyError:
        raise ValueError(
            f"No known raw-data S3 bucket for satellite {satellite!r} "
            f"(known: {sorted(_SATELLITE_TO_BUCKET)})"
        ) from None


def _sector_s3_folder(sector: str) -> str:
    try:
        return _SECTOR_TO_S3_FOLDER[sector]
    except KeyError:
        raise ValueError(
            f"source_kind = \"satpy_raw\" has no S3 mapping for sector {sector!r} "
            f"(known: {sorted(_SECTOR_TO_S3_FOLDER)})"
        ) from None


def _sector_s3_file_token(sector: str) -> str:
    return _SECTOR_TO_S3_FILE_TOKEN[sector]


def _parse_band_and_scan_token(key: str) -> tuple[int, str] | None:
    m = _BAND_AND_SCAN_RE.search(key)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def _scan_token_to_iso(token: str) -> str:
    """Convert an ABI filename scan-start token (sYYYYDDDHHMMSSt) to ISO8601 UTC.
    Only the first 13 digits (year/day-of-year/hour/min/sec) are used; the trailing
    tenths-of-a-second digit is dropped -- second precision is plenty for our
    freshness comparison and metadata display."""
    dt = datetime.strptime(token[:13], "%Y%j%H%M%S").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _select_latest_complete_scan(keys: Iterable[str], required_bands: set[int]) -> _ScanSelection | None:
    """Group S3 object keys by their scan-start-time token, keep only groups that
    have every required band, and return the most recent complete one (or None if
    no group is complete -- e.g. a scan is only partially uploaded yet). Pure
    function over plain filename strings, no S3/satpy dependency -- this is the
    unit-testable core of the freshness/selection logic."""
    by_token: dict[str, dict[int, str]] = {}
    for key in keys:
        parsed = _parse_band_and_scan_token(key)
        if parsed is None:
            continue
        band, token = parsed
        if band not in required_bands:
            continue
        by_token.setdefault(token, {})[band] = key

    complete = {token: bands for token, bands in by_token.items() if required_bands <= bands.keys()}
    if not complete:
        return None
    newest_token = max(complete)  # fixed-width zero-padded tokens sort lexically == chronologically
    return _ScanSelection(
        scan_time_utc=_scan_token_to_iso(newest_token),
        scan_time_token=newest_token,
        keys=complete[newest_token],
    )


def _list_latest_complete_scan(satellite: str, sector: str, lookback_minutes: float) -> _ScanSelection | None:
    check_available()
    import s3fs

    fs = s3fs.S3FileSystem(anon=True)
    bucket = _bucket_for_satellite(satellite)
    folder = _sector_s3_folder(sector)
    token = _sector_s3_file_token(sector)

    now = datetime.now(timezone.utc)
    hours_back = max(1, math.ceil(lookback_minutes / 60) + 1)
    keys: list[str] = []
    for hours_ago in range(hours_back):
        hour = now - timedelta(hours=hours_ago)
        prefix = f"{bucket}/{folder}/{hour:%Y}/{hour.timetuple().tm_yday:03d}/{hour:%H}/"
        try:
            keys.extend(fs.ls(prefix))
        except FileNotFoundError:
            continue

    keys = [k for k in keys if f"-{token}-" in k]
    return _select_latest_complete_scan(keys, _REQUIRED_BANDS)


def find_latest_scan_time(satellite: str, sector: str, lookback_minutes: float = 20.0) -> str | None:
    """List the bucket for satellite/sector's most recent scan with a complete band
    set, searching back `lookback_minutes` of hourly prefixes. Returns the scan's
    start time (ISO8601 UTC), or None if nothing complete was found in that window
    (satellite offline, coverage gap, etc). Pure listing, no download."""
    selection = _list_latest_complete_scan(satellite, sector, lookback_minutes)
    return selection.scan_time_utc if selection else None


def fetch_composite(
    satellite: str,
    sector: str,
    prev_scan_time_utc: str | None,
    work_dir: Path,
    lookback_minutes: float = 20.0,
) -> RawFetchResult | None:
    """Mirrors fetch_fresh_image's shape for the satpy_raw path: lists the bucket,
    compares the latest complete scan's time against prev_scan_time_utc, and
    returns None if unchanged -- this IS the 304-equivalent for this source kind,
    there's no HTTP ETag involved. Otherwise downloads the required band files into
    work_dir (no cross-cycle caching in v1 -- every prior cycle's band files are
    deleted first, since each scan's filenames are scan-unique and would otherwise
    just accumulate forever) and composites them into a GeoColor-style image (see
    _composite_true_color_with_muted_ir_night)."""
    check_available()
    import s3fs

    selection = _list_latest_complete_scan(satellite, sector, lookback_minutes)
    if selection is None:
        logging.warning(
            "[satpy_raw] No complete band set found for %s/%s in the last %.0f minutes",
            satellite, sector, lookback_minutes,
        )
        return None
    if selection.scan_time_utc == prev_scan_time_utc:
        return None

    fs = s3fs.S3FileSystem(anon=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Scan-unique filenames (e.g. "...C02_G18_s20241601801173..."): without this,
    # every cycle's band files pile up in work_dir forever instead of being reused
    # or replaced (~98MB/cycle for CONUS, ~550MB for Full Disk -- a 5-minute --loop
    # would otherwise leak tens of GB/day). Clear stale files before downloading the
    # current selection so peak usage stays at roughly one cycle's worth.
    keep_names = {Path(key).name for key in selection.keys.values()}
    for existing in work_dir.iterdir():
        if existing.is_file() and existing.name not in keep_names:
            existing.unlink()

    local_files = []
    total_bytes = 0
    download_started = time.monotonic()
    for _band, key in sorted(selection.keys.items()):
        local_path = work_dir / Path(key).name
        fs.get(key, str(local_path))
        local_files.append(str(local_path))
        total_bytes += local_path.stat().st_size
    logging.info(
        "[satpy_raw] Downloaded %d bytes (%d band files) for %s/%s scan %s in %.2fs",
        total_bytes, len(local_files), satellite, sector, selection.scan_time_utc,
        time.monotonic() - download_started,
    )

    image, area = _composite_true_color_with_muted_ir_night(local_files)
    return RawFetchResult(
        image=image,
        scan_time_utc=selection.scan_time_utc,
        extent=tuple(area.area_extent),
        proj4_params=area.crs.to_dict(),
        band_files=list(selection.keys.values()),
        total_bytes=total_bytes,
    )


def _muted_ir_night_rgb(c13_bt: np.ndarray) -> np.ndarray:
    """Map Band 13 (clean IR window, works day and night) brightness temperature to
    a muted night-side color, as [0, 1] float RGB with shape (*c13_bt.shape, 3).
    See _NIGHT_WARM_RGB/_NIGHT_COLD_RGB/_NIGHT_BT_*_K module comments for the
    tuning."""
    t = np.clip((_NIGHT_BT_WARM_K - c13_bt) / (_NIGHT_BT_WARM_K - _NIGHT_BT_COLD_K), 0.0, 1.0)
    warm = np.asarray(_NIGHT_WARM_RGB, dtype=np.float32) / 255.0
    cold = np.asarray(_NIGHT_COLD_RGB, dtype=np.float32) / 255.0
    return warm + t[..., np.newaxis] * (cold - warm)


def _blend_day_night(day_rgb: np.ndarray, night_rgb: np.ndarray, coszen: np.ndarray) -> np.ndarray:
    """Blend day_rgb/night_rgb ([0, 1] float, matching shape) by cos(solar zenith
    angle), using the same terminator softness as satpy's own geo_color composite
    (_NIGHT_BLEND_LIM_LOW_DEG/_HIGH_DEG). 1 = full day, 0 = full night."""
    lim_low_cos = math.cos(math.radians(_NIGHT_BLEND_LIM_LOW_DEG))
    lim_high_cos = math.cos(math.radians(_NIGHT_BLEND_LIM_HIGH_DEG))
    day_weight = (coszen - min(lim_low_cos, lim_high_cos)) / abs(lim_low_cos - lim_high_cos)
    day_weight = np.clip(day_weight, 0.0, 1.0)[..., np.newaxis]
    return day_rgb * day_weight + night_rgb * (1.0 - day_weight)


def _target_area(scn: Any) -> Any:
    """A version of the scene's native (finest-loaded-band) area, downscaled to
    roughly _COMPOSITE_TARGET_WIDTH_PX wide with the sector's native aspect ratio
    preserved. Passed as the explicit `destination` for Scene.resample() instead of
    the "native" resampler's own default (upsample everything to finest, i.e. no
    change in resolution) -- see _COMPOSITE_TARGET_WIDTH_PX's comment for why.

    The downscale factor is snapped to the nearest power of 2 (1, 2, 4, 8, ...)
    rather than hitting the target width exactly: satpy's NativeResampler only
    aggregates (averages down) by an exact integer ratio of *each individual
    band's own native resolution* to the destination, not just the finest band's --
    found by actually hitting "Aggregation factors are not integers" on a live Full
    Disk fetch. ABI's own band resolutions (0.5km/1km/2km) are themselves
    power-of-2 multiples of each other, so a power-of-2 factor off the finest
    band's native width stays an exact integer ratio for every coarser band too,
    without needing to special-case which bands are loaded."""
    native = scn.finest_area()
    ideal_factor = native.width / _COMPOSITE_TARGET_WIDTH_PX
    factor = 1
    while factor * 2 <= ideal_factor:
        factor *= 2
    target_width = native.width // factor
    target_height = native.height // factor
    return native.copy(width=target_width, height=target_height)


def _composite_true_color_with_muted_ir_night(local_files: list[str]) -> tuple[Image.Image, Any]:
    """GeoColor-style composite, built ourselves instead of using satpy's stock
    geo_color: true_color (day, from C01/C02/C03) blended at the terminator into a
    muted-color night layer derived from C13 (see _muted_ir_night_rgb), using the
    real per-pixel solar zenith angle (satpy.modifiers.angles.get_cos_sza) for the
    blend weight.

    Built this way instead of using satpy's stock geo_color composite for two
    reasons: (1) geo_color's night layer needs a NASA-hosted Black Marble
    night-lights file that currently 404s (an external outage, not something in our
    control -- see CUSTOM_IMAGERY_PLAN.md); (2) even when that file is reachable,
    geo_color's night rendering is a static, non-real-time city-lights composite,
    which this project has deliberately steered away from throughout (see
    CUSTOM_IMAGERY_PLAN.md's original recommendation) -- this gives a deliberately
    photographic-feeling middle ground instead: real per-cycle IR data, muted/
    desaturated rather than "fake." Real VIIRS Day/Night Band city lights would be
    a genuine (real, not static) upgrade over this -- see CUSTOM_IMAGERY_PLAN.md's
    backlog."""
    from satpy import Scene
    from satpy.modifiers.angles import get_cos_sza
    from satpy.writers import get_enhanced_image

    scn = Scene(reader="abi_l1b", filenames=local_files)
    scn.load(["true_color", "C13"], generate=False)
    scn = scn.resample(destination=_target_area(scn), resampler="native")

    true_color = scn["true_color"]
    area = true_color.attrs["area"]

    day_img = get_enhanced_image(true_color).pil_image().convert("RGB")
    day_rgb = np.asarray(day_img, dtype=np.float32) / 255.0

    c13_bt = np.asarray(scn["C13"].values, dtype=np.float32)
    night_rgb = _muted_ir_night_rgb(c13_bt)

    coszen = np.asarray(get_cos_sza(scn["C13"]).values, dtype=np.float32)
    blended = _blend_day_night(day_rgb, night_rgb, coszen)

    image = Image.fromarray(np.clip(blended * 255.0, 0, 255).astype(np.uint8), mode="RGB")
    return image, area
