# tests/conftest.py -- test scaffolding for goes_wallpaper
# Copyright (C) 2026 John-Schreiber
# SPDX-License-Identifier: GPL-3.0-or-later

"""Makes the repo root importable, so `import goes_wallpaper` (goes_wallpaper.py) and
`import platform_base`/`platform_windows` resolve without installing the package
first."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
