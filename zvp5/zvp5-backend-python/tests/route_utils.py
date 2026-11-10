"""Compatibility helpers for FastAPI's lazily-included router tree."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from fastapi.routing import _IncludedRouter


def iter_leaf_routes(routes: Iterable) -> Iterator:
    """Yield concrete routes through FastAPI's included-router wrappers."""
    for route in routes:
        if isinstance(route, _IncludedRouter):
            yield from iter_leaf_routes(route.effective_candidates())
        else:
            yield route
