# tests/test_source_satpy.py -- pure-function tests for source_satpy.py
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Covers the parts of source_satpy.py that don't need satpy/pyresample/s3fs
actually installed or reachable: scan/band selection over plain filename strings,
and the sector-name mapping table. Real bucket listing and satpy compositing
(check_available()'s happy path, find_latest_scan_time, fetch_composite) are
manual-only, same status as goes_wallpaper.py's live CDN fetch path -- see
CONTRIBUTING.md."""

from pathlib import Path

import pytest
from PIL import Image

import source_satpy


def _key(band: int, scan_token: str, sector_token: str = "RadC") -> str:
    return (
        f"noaa-goes18/ABI-L1b-RadC/2024/160/18/"
        f"OR_ABI-L1b-{sector_token}-M6C{band:02d}_G18_s{scan_token}_e20241601803546_c20241601803584.nc"
    )


class TestSelectLatestCompleteScan:
    def test_returns_none_for_empty_list(self):
        assert source_satpy._select_latest_complete_scan([], {1, 2, 3, 13}) is None

    def test_returns_none_when_no_scan_has_all_required_bands(self):
        keys = [_key(1, "20241601801173"), _key(2, "20241601801173")]
        assert source_satpy._select_latest_complete_scan(keys, {1, 2, 3, 13}) is None

    def test_picks_the_newest_complete_scan(self):
        older, newer = "20241601801173", "20241601806173"
        keys = [_key(b, older) for b in (1, 2, 3, 13)] + [_key(b, newer) for b in (1, 2, 3, 13)]
        selection = source_satpy._select_latest_complete_scan(keys, {1, 2, 3, 13})
        assert selection is not None
        assert selection.scan_time_token == newer
        assert set(selection.keys) == {1, 2, 3, 13}

    def test_ignores_a_partially_uploaded_newer_scan(self):
        complete, partial = "20241601801173", "20241601806173"
        keys = [_key(b, complete) for b in (1, 2, 3, 13)] + [_key(1, partial)]  # only band 1 present
        selection = source_satpy._select_latest_complete_scan(keys, {1, 2, 3, 13})
        assert selection.scan_time_token == complete

    def test_bands_outside_required_set_are_ignored(self):
        token = "20241601801173"
        keys = [_key(b, token) for b in (1, 2, 3, 13)] + [_key(7, token)]  # band 7 not required
        selection = source_satpy._select_latest_complete_scan(keys, {1, 2, 3, 13})
        assert set(selection.keys) == {1, 2, 3, 13}

    def test_unparseable_keys_are_skipped(self):
        keys = [_key(b, "20241601801173") for b in (1, 2, 3, 13)] + ["not-a-real-key.txt"]
        selection = source_satpy._select_latest_complete_scan(keys, {1, 2, 3, 13})
        assert selection is not None

    def test_scan_time_utc_is_parsed_correctly(self):
        # Day-of-year 160 in 2024 (a leap year) is June 8.
        keys = [_key(b, "20241601801173") for b in (1, 2, 3, 13)]
        selection = source_satpy._select_latest_complete_scan(keys, {1, 2, 3, 13})
        assert selection.scan_time_utc == "2024-06-08T18:01:17+00:00"


class TestSectorMapping:
    def test_conus_folder_and_file_token(self):
        assert source_satpy._sector_s3_folder("CONUS") == "ABI-L1b-RadC"
        assert source_satpy._sector_s3_file_token("CONUS") == "RadC"

    def test_full_disk_folder_and_file_token(self):
        assert source_satpy._sector_s3_folder("FD") == "ABI-L1b-RadF"
        assert source_satpy._sector_s3_file_token("FD") == "RadF"

    def test_mesoscale_sectors_share_one_folder_but_differ_in_file_token(self):
        assert source_satpy._sector_s3_folder("M1") == source_satpy._sector_s3_folder("M2") == "ABI-L1b-RadM"
        assert source_satpy._sector_s3_file_token("M1") == "RadM1"
        assert source_satpy._sector_s3_file_token("M2") == "RadM2"

    def test_unknown_sector_raises(self):
        with pytest.raises(ValueError, match="no S3 mapping"):
            source_satpy._sector_s3_folder("BOGUS")


class TestBucketForSatellite:
    @pytest.mark.parametrize("satellite,bucket", [
        ("GOES16", "noaa-goes16"), ("GOES18", "noaa-goes18"), ("GOES19", "noaa-goes19"),
    ])
    def test_known_satellites(self, satellite, bucket):
        assert source_satpy._bucket_for_satellite(satellite) == bucket

    def test_unknown_satellite_raises(self):
        with pytest.raises(ValueError, match="No known raw-data S3 bucket"):
            source_satpy._bucket_for_satellite("GOES99")


def test_fetch_composite_deletes_stale_band_files_before_downloading(monkeypatch, tmp_path):
    """Regression test for the satpy_raw_cache disk leak: work_dir must not
    accumulate previous cycles' band files (scan-unique names) forever."""
    try:
        import s3fs
    except ImportError:
        pytest.skip("s3fs (satpy-raw extra) not installed in this environment")

    stale = tmp_path / "OR_ABI-L1b-RadC-M6C01_G18_s20241601700000_e20241601703373_c20241601703410.nc"
    stale.write_bytes(b"stale")

    new_token = "20241601801173"
    new_keys = {b: _key(b, new_token) for b in (1, 2, 3, 13)}
    selection = source_satpy._ScanSelection(
        scan_time_utc="2024-06-08T18:01:17+00:00", scan_time_token=new_token, keys=new_keys,
    )
    monkeypatch.setattr(source_satpy, "_list_latest_complete_scan", lambda *a, **k: selection)

    class FakeS3FileSystem:
        def __init__(self, anon=True):
            pass

        def get(self, key, local_path):
            Path(local_path).write_bytes(b"data")

    monkeypatch.setattr(s3fs, "S3FileSystem", FakeS3FileSystem)

    class FakeCRS:
        def to_dict(self):
            return {}

    class FakeArea:
        area_extent = (0, 0, 1, 1)
        crs = FakeCRS()

    fake_image = Image.new("RGB", (2, 2))
    monkeypatch.setattr(
        source_satpy, "_composite_true_color_with_muted_ir_night",
        lambda files: (fake_image, FakeArea()),
    )

    result = source_satpy.fetch_composite("GOES18", "CONUS", None, tmp_path)

    assert result is not None
    assert not stale.exists()
    for key in new_keys.values():
        assert (tmp_path / Path(key).name).exists()


def test_check_available_raises_actionable_error_when_satpy_not_installed():
    try:
        import satpy  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("satpy is installed in this environment; can't exercise the unavailable path")

    with pytest.raises(source_satpy.SatpyUnavailableError, match="satpy-raw"):
        source_satpy.check_available()
