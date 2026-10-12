"""MongoDB implementations of the ``db.repos.protocols`` interfaces.

These are the flag-off path of the incremental Mongo → Oracle migration: each
class mirrors the semantics and row shapes of its ``db.repos.oracle`` sibling
as closely as the current motor queries allow, so flipping
``settings.ORACLE_MODULES`` does not change API responses.

No DB I/O happens at import time.
"""
