# tests/test_instance_lock.py -- acquire_instance_lock
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""acquire_instance_lock stops a second goes_wallpaper process from running against
the same data_dir concurrently -- two racing instances have no synchronization over
state.json/wallpaper.jpg/log.txt, so whichever cycle finishes last silently wins
regardless of which one fetched the fresher capture. These pin down that a second
attempt while the first is still held is rejected, that closing the handle actually
releases the OS-level lock (a crashed process's handle table going away should have
the same effect), and that the lock file itself is created on demand."""

import goes_wallpaper as gw


class TestAcquireInstanceLock:
    def test_first_caller_gets_the_lock(self, tmp_path):
        cfg = gw.Config(data_dir=tmp_path)
        handle = gw.acquire_instance_lock(cfg)
        try:
            assert handle is not None
            assert cfg.lock_path.exists()
        finally:
            handle.close()

    def test_second_concurrent_caller_is_rejected(self, tmp_path):
        cfg = gw.Config(data_dir=tmp_path)
        first = gw.acquire_instance_lock(cfg)
        try:
            second = gw.acquire_instance_lock(cfg)
            assert second is None
        finally:
            first.close()

    def test_releasing_the_handle_lets_the_next_caller_acquire_it(self, tmp_path):
        cfg = gw.Config(data_dir=tmp_path)
        first = gw.acquire_instance_lock(cfg)
        first.close()

        second = gw.acquire_instance_lock(cfg)
        try:
            assert second is not None
        finally:
            second.close()

    def test_creates_data_dir_if_missing(self, tmp_path):
        cfg = gw.Config(data_dir=tmp_path / "not_yet_created")
        handle = gw.acquire_instance_lock(cfg)
        try:
            assert handle is not None
            assert cfg.data_dir.is_dir()
        finally:
            handle.close()
