"""Pytest bootstrap for ``tests/golden/statistics``.

Makes the repository root (so ``tasks`` / ``db`` / ``util`` import) and this
directory (so the sibling harness modules ``fixture`` / ``dump`` / ``compare`` /
``reference`` import) available regardless of how pytest is invoked:

    pytest tests/golden/statistics/test_tier1.py -q
    python -m pytest tests/golden/statistics/test_tier1.py -q

Imports no database code and performs no I/O beyond ``sys.path`` setup.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/statistics -> <repo root>

for _path in (str(HERE), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
