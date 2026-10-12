"""Repository protocols for the data access layer.

This package holds the backend-agnostic interfaces (see :mod:`db.repos.protocols`)
that both the Mongo adapters and the Oracle implementations must satisfy. It is
pure interface code: no I/O and no concrete implementations live here yet.

The concrete ``mongo`` and ``oracle`` adapters, plus the per-module factory in
:mod:`db.backends`, land in the next round.
"""
