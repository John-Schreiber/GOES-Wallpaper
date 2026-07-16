# tests/test_smoke.py -- confirm the module-loading shim in conftest.py works
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

import goes_wallpaper as gw


def test_module_loads():
    assert gw.Config is not None
    cfg = gw.Config()
    assert cfg.satellite == "GOES19"
