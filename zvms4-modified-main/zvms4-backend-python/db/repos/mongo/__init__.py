"""MongoDB implementations of the ``db.repos.protocols`` interfaces.

Each class mirrors the semantics and row shapes pinned by its protocol so
callers in ``routers/`` and ``util/`` are drop-in compatible.

No DB I/O happens at import time.
"""
