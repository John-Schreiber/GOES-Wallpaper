# tests/test_scheduling.py -- compute_next_run, update_capture_phase, maybe_wait_for_sync
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import datetime, timezone

import goes_wallpaper as gw


def iso_at(epoch_seconds):
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


class TestUpdateCapturePhase:
    def test_first_observation_sets_phase_directly(self):
        cfg = gw.Config(interval_minutes=5)
        state = {}
        boundary = 1_700_000_400  # divisible by 300
        gw.update_capture_phase(cfg, state, iso_at(boundary + 40))
        assert state["capture_phase_seconds"] == 40.0
        assert state["capture_phase_interval_minutes"] == 5

    def test_converges_toward_new_observations(self):
        cfg = gw.Config(interval_minutes=5)
        state = {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 5}
        boundary = 1_700_000_400
        gw.update_capture_phase(cfg, state, iso_at(boundary + 300 + 46))  # next interval, phase=46
        # EMA with alpha=0.3 toward diff=+6 -> 40 + 0.3*6 = 41.8
        assert state["capture_phase_seconds"] == 41.8

    def test_wraparound_near_interval_boundary_does_not_jump(self):
        cfg = gw.Config(interval_minutes=5)
        state = {"capture_phase_seconds": 298.0, "capture_phase_interval_minutes": 5}
        boundary = 1_700_000_400
        # New capture at phase=2 (i.e. 2s into the *next* interval) -- circularly only +4 away, not -296
        gw.update_capture_phase(cfg, state, iso_at(boundary + 300 + 2))
        assert 298.0 < state["capture_phase_seconds"] < 300.0 or state["capture_phase_seconds"] < 2.0

    def test_interval_change_resets_learning(self):
        cfg = gw.Config(interval_minutes=10)  # different interval than what's stored
        state = {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 5}
        boundary = 1_700_000_400
        gw.update_capture_phase(cfg, state, iso_at(boundary + 90))
        assert state["capture_phase_seconds"] == 90.0
        assert state["capture_phase_interval_minutes"] == 10


class TestComputeNextRun:
    def test_no_clock_alignment_just_adds_interval(self):
        cfg = gw.Config(interval_minutes=5, align_to_clock=False)
        assert gw.compute_next_run(cfg, {}, 1000.0) == 1300.0

    def test_no_learned_phase_falls_back_to_raw_boundary(self):
        cfg = gw.Config(interval_minutes=5, align_to_clock=True, sync_to_capture_time=True)
        now = 1_700_000_450  # 50s past a 300s boundary
        boundary = (now // 300) * 300
        assert gw.compute_next_run(cfg, {}, now) == boundary + 300

    def test_disabled_sync_ignores_learned_phase(self):
        cfg = gw.Config(interval_minutes=5, sync_to_capture_time=False)
        state = {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 5}
        now = 1_700_000_450
        boundary = (now // 300) * 300
        assert gw.compute_next_run(cfg, state, now) == boundary + 300

    def test_learned_phase_targets_boundary_plus_phase_plus_buffer(self):
        cfg = gw.Config(interval_minutes=5, capture_offset_buffer_seconds=20.0)
        state = {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 5}
        boundary = 1_700_000_400
        now = boundary + 2  # just after boundary, target (40+20=60) still ahead
        assert gw.compute_next_run(cfg, state, now) == boundary + 60

    def test_target_already_passed_rolls_to_next_interval(self):
        cfg = gw.Config(interval_minutes=5, capture_offset_buffer_seconds=20.0)
        state = {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 5}
        boundary = 1_700_000_400
        now = boundary + 100  # target (60) already passed
        assert gw.compute_next_run(cfg, state, now) == boundary + 300 + 60

    def test_stale_learned_interval_falls_back_to_raw_boundary(self):
        cfg = gw.Config(interval_minutes=5)
        state = {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 10}  # mismatched
        now = 1_700_000_450
        boundary = (now // 300) * 300
        assert gw.compute_next_run(cfg, state, now) == boundary + 300


class TestMaybeWaitForSync:
    def test_disabled_never_sleeps(self, monkeypatch):
        cfg = gw.Config(wait_for_sync_time=False)
        state = {"sources": {"k": {"capture_phase_seconds": 40.0, "capture_phase_interval_minutes": 5}}}
        source = gw.resolve_source(cfg, None)
        slept = []
        monkeypatch.setattr(gw.time, "sleep", lambda s: slept.append(s))
        gw.maybe_wait_for_sync(cfg, {"sources": {source.key: state["sources"]["k"]}}, source)
        assert slept == []

    def test_no_learned_phase_does_not_sleep(self, monkeypatch):
        cfg = gw.Config(wait_for_sync_time=True)
        source = gw.resolve_source(cfg, None)
        slept = []
        monkeypatch.setattr(gw.time, "sleep", lambda s: slept.append(s))
        gw.maybe_wait_for_sync(cfg, {"sources": {}}, source)
        assert slept == []

    def test_sleeps_for_computed_wait_when_target_is_future(self, monkeypatch):
        cfg = gw.Config(interval_minutes=5, wait_for_sync_time=True, capture_offset_buffer_seconds=20.0)
        source = gw.resolve_source(cfg, None)
        boundary = 1_700_000_400
        fake_now = boundary + 2.0
        state = {"sources": {source.key: {"capture_phase_seconds": 58.0, "capture_phase_interval_minutes": 5}}}

        monkeypatch.setattr(gw.time, "time", lambda: fake_now)
        slept = []
        monkeypatch.setattr(gw.time, "sleep", lambda s: slept.append(s))
        gw.maybe_wait_for_sync(cfg, state, source)
        assert slept == [76.0]  # 58 + 20 - 2

    def test_exceeding_max_wait_skips_sleep(self, monkeypatch):
        cfg = gw.Config(
            interval_minutes=5, wait_for_sync_time=True,
            capture_offset_buffer_seconds=20.0, wait_for_sync_max_seconds=10.0,
        )
        source = gw.resolve_source(cfg, None)
        boundary = 1_700_000_400
        fake_now = boundary + 2.0
        state = {"sources": {source.key: {"capture_phase_seconds": 58.0, "capture_phase_interval_minutes": 5}}}

        monkeypatch.setattr(gw.time, "time", lambda: fake_now)
        slept = []
        monkeypatch.setattr(gw.time, "sleep", lambda s: slept.append(s))
        gw.maybe_wait_for_sync(cfg, state, source)
        assert slept == []

    def test_target_already_passed_does_not_sleep(self, monkeypatch):
        cfg = gw.Config(interval_minutes=5, wait_for_sync_time=True, capture_offset_buffer_seconds=20.0)
        source = gw.resolve_source(cfg, None)
        boundary = 1_700_000_400
        fake_now = boundary + 200.0  # well past target (58+20=78)
        state = {"sources": {source.key: {"capture_phase_seconds": 58.0, "capture_phase_interval_minutes": 5}}}

        monkeypatch.setattr(gw.time, "time", lambda: fake_now)
        slept = []
        monkeypatch.setattr(gw.time, "sleep", lambda s: slept.append(s))
        gw.maybe_wait_for_sync(cfg, state, source)
        assert slept == []


class TestNextCycleSourceKey:
    """run_loop schedules its next wake-up off the phase of whichever source
    _next_cycle_source_key names -- these pin down that in "rotate" mode this must
    be the *upcoming* combo (state["combo_rotation_index"], already advanced by
    run_once_rotate before it saves state), not state["last_source_key"] (the combo
    *just* fetched, whose publish phase can differ)."""

    def _combos(self):
        return (
            gw.Combo(name="a", satellite="GOES18", sector="CONUS"),
            gw.Combo(name="b", satellite="GOES19", sector="FD"),
        )

    def test_rotate_mode_uses_the_upcoming_combo_not_the_last_fetched_one(self):
        combos = self._combos()
        cfg = gw.Config(combo_mode="rotate", combos=combos)
        # combo_rotation_index=1 means run_once_rotate just fetched combos[0] and
        # advanced the index to point at combos[1] for next cycle.
        state = {
            "combo_rotation_index": 1,
            "last_source_key": gw.resolve_source(cfg, combos[0]).key,
        }
        key = gw._next_cycle_source_key(cfg, state)
        assert key == gw.resolve_source(cfg, combos[1]).key
        assert key != state["last_source_key"]

    def test_rotate_mode_wraps_the_index(self):
        combos = self._combos()
        cfg = gw.Config(combo_mode="rotate", combos=combos)
        state = {"combo_rotation_index": 0}  # wrapped back to the first combo
        assert gw._next_cycle_source_key(cfg, state) == gw.resolve_source(cfg, combos[0]).key

    def test_single_mode_uses_last_source_key(self):
        cfg = gw.Config(combo_mode="single")
        state = {"last_source_key": "some/source/key"}
        assert gw._next_cycle_source_key(cfg, state) == "some/source/key"

    def test_per_monitor_mode_has_no_single_key(self):
        # run_once_per_monitor never writes last_source_key (several sources are
        # fetched per cycle, no single one to name) -- falls back to clock-boundary
        # alignment via the None here, same as an empty/fresh state.
        cfg = gw.Config(combo_mode="per_monitor", combos=self._combos())
        assert gw._next_cycle_source_key(cfg, {}) is None
