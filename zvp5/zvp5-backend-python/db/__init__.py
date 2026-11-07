"""Database access layer.

The application uses Oracle Autonomous Database through ``db.backends`` and
the async repositories in ``db.repos.oracle``. Engine/session construction
is lazy; importing this package performs no database I/O.
"""
