"""Database access layer.

This package is intentionally self-contained and is not wired into the
application yet. The current application uses MongoDB (see ``database.py``);
the ``db.oracle`` subpackage only provides Oracle Autonomous Database (ADB)
connection/config scaffolding and performs no I/O at import time.
"""
