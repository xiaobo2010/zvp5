"""Pytest bootstrap for ``tests/golden/attachments``.

Makes the repository root importable (``db`` / ``routers`` / ``util`` /
``typings``) regardless of how pytest is invoked:

    pytest tests/golden/attachments/ -q
    python -m pytest tests/golden/attachments/ -q

Imports no database code and performs no I/O beyond ``sys.path`` setup.

Offline compromise: importing ``routers.attachments_router`` reaches
``util.object_id``, which reads ``aes_key.txt`` from the current directory at
import time. Run pytest from the **repository root** (as the acceptance command
does); a run from elsewhere fails at collection, not in an assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/attachments -> <repo root>

for _path in (str(HERE), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
