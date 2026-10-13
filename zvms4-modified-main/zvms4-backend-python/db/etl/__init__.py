"""One-way ETL utilities (MongoDB ``zvms_new`` -> Oracle ADB).

This package holds offline migration tooling. Importing it performs **no
database I/O** and does not import the Oracle engine.

* :mod:`db.etl.mongo_to_adb` - the idempotent, one-directional collection mover
  (``--dry-run`` default; ``--execute`` to write).
"""

from __future__ import annotations

__all__ = ["mongo_to_adb"]
