"""Pytest bootstrap for ``tests/golden/auth``.

Makes the repository root (so ``routers`` / ``db`` / ``util`` / ``typings``
import) available regardless of how pytest is invoked:

    pytest tests/golden/auth/test_login_logging.py -q
    python -m pytest tests/golden/auth/test_login_logging.py -q

Imports no database code and performs no I/O beyond ``sys.path`` setup.

Importing ``routers.users_router`` reaches ``util.object_id``, which reads
``aes_key.txt`` from the *current directory* at import time, so pytest must be
run from the repository root (as the acceptance command does); a run from
elsewhere fails at collection, not in an assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/auth -> <repo root>

for _path in (str(HERE), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
