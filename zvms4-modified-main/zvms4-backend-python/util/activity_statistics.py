"""Pure helpers behind the per-activity statistics endpoints.

Everything here is a plain function over an in-memory sequence of rows: no
database, no network, no file I/O. ``routers/activity_statistics_router`` and
``routers/mcp_router`` own the reads and reuse these helpers, so the histogram,
the grouped views, the description and the MCP mapping all share one
implementation of the maths.

Run the offline unit tests with pytest (repo root on ``sys.path`` via
``conftest.py``)::

    pytest tests/golden/statistics/test_activity_stats.py -q
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

import numpy as np

BIN_WIDTH_SMALL = 0.5
BIN_WIDTH_LARGE = 1.0
BIN_WIDTH_THRESHOLD = 10.0
MAX_BINS = 1000

MODE_ORDER = ["on-campus", "off-campus", "social-practice"]


def pick_bin_width(max_value: float) -> float:
    """Return the histogram bin width for a sample whose largest value is ``max_value``.

    Half-hour bins up to ``BIN_WIDTH_THRESHOLD`` hours, whole hours above it.
    The width then doubles while that would still leave more than ``MAX_BINS``
    bins, so a corrupt duration (the v2 payload carries a bare ``float``, so
    ``1e9`` is reachable) cannot ask for a billion bins. Every realistic
    magnitude stays on the plain rule: the first value that widens is 1000.0
    hours, which would need 1001 bins.

    :param max_value: Largest duration of the sample, in hours
    :return: ``BIN_WIDTH_SMALL`` or ``BIN_WIDTH_LARGE``, doubled if needed
    """
    width = BIN_WIDTH_SMALL if max_value <= BIN_WIDTH_THRESHOLD else BIN_WIDTH_LARGE
    while math.floor(max_value / width) + 1 > MAX_BINS:
        width *= 2
    return width


def build_bins(durations: Sequence[float], width: float) -> list[dict]:
    """Bucket ``durations`` into half-open ``[i * width, (i + 1) * width)`` bins.

    The bin count is ``floor(max / width) + 1`` (at least one, so a wholly
    negative sample still yields a histogram instead of an empty list) so the
    largest duration always lands in the last bin, and the counts always add up
    to ``len(durations)``. Both ends are clamped: a negative duration is counted
    into the first bin, never into the last one through Python's negative
    indexing.

    :param durations: Durations in hours; empty input yields ``[]``
    :param width: Bin width in hours, usually from :func:`pick_bin_width`
    :return: ``[{"from", "to", "count"}]``, ascending, with no trailing empty bin
    """
    if not durations:
        return []
    n = max(1, int(math.floor(max(durations) / width)) + 1)
    bins = [{"from": i * width, "to": (i + 1) * width, "count": 0} for i in range(n)]
    for duration in durations:
        idx = max(0, min(int(duration // width), n - 1))
        bins[idx]["count"] += 1
    return bins


def describe_durations(durations: Sequence[float]) -> dict | None:
    """Return the descriptive statistics of ``durations``, or ``None`` when empty.

    ``mean`` / ``median`` / ``std`` / ``var`` / the percentiles come from numpy
    (population statistics, linear interpolation) and ``mode`` is the most
    frequent value, ties resolved to the smallest one. Every value is a plain
    Python scalar so the result is JSON-serialisable as-is.

    :param durations: Durations in hours
    :return: ``{"mean", "median", "mode", "std", "min", "max", "var", "sum",
        "25_percentile", "75_percentile", "total"}``, or ``None`` for empty input
    """
    if not durations:
        return None
    values = np.array([float(duration) for duration in durations], dtype=np.float64)
    counts = Counter(durations)
    most_common = max(counts.values())
    return {
        "mean": np.mean(values).item(),
        "median": float(np.median(values)),
        "mode": float(min(value for value, count in counts.items() if count == most_common)),
        "std": np.std(values).item(),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "var": np.var(values).item(),
        "sum": float(np.sum(values)),
        "25_percentile": np.percentile(values, 25).item(),
        "75_percentile": np.percentile(values, 75).item(),
        "total": len(durations),
    }


def group_durations(
    entries: Sequence[tuple[str, float]], order: Sequence[str] | None = None
) -> list[dict]:
    """Aggregate ``(key, duration)`` pairs into one ``{"key", "count", "sum", "mean"}`` row per key.

    Only keys that occur in ``entries`` are reported. With ``order`` the rows
    follow that sequence first (unordered keys are appended, ascending); without
    it the rows are sorted by ``sum`` descending, ties by key ascending.

    :param entries: ``(key, duration in hours)`` pairs, e.g. ``(mode, duration)``
    :param order: Preferred key order, or ``None`` for the sum-descending order
    :return: Aggregated rows
    """
    buckets: dict[str, list[float]] = {}
    for key, duration in entries:
        buckets.setdefault(key, []).append(float(duration))
    rows = []
    for key, values in buckets.items():
        total = float(sum(values))
        rows.append(
            {
                "key": key,
                "count": len(values),
                "sum": total,
                "mean": total / len(values),
            }
        )
    if order is None:
        rows.sort(key=lambda row: (-row["sum"], row["key"]))
        return rows
    rank = {key: index for index, key in enumerate(order)}
    rows.sort(key=lambda row: (rank.get(row["key"], len(order)), row["key"]))
    return rows


def empty_analysis_payload() -> dict:
    """Return a fresh empty ``/analysis`` payload.

    A new object (and new lists) on every call, so callers never share mutable
    state between responses.

    :return: The six keys of the analysis contract, zeroed out
    """
    return {
        "total": 0,
        "bin_width": BIN_WIDTH_SMALL,
        "description": None,
        "bins": [],
        "modes": [],
        "classes": [],
    }


def build_analysis_payload(
    members: Sequence[dict], users: Sequence[dict], classes: Sequence[dict]
) -> dict:
    """Assemble the ``/analysis`` payload from rows the caller has already read.

    ``members`` rows carry ``member`` (the user oid) / ``mode`` / ``duration``
    and must already be the effective-only population — this function does not
    filter on status. ``users`` rows carry ``_id`` / ``class_id`` and ``classes``
    rows ``_id`` / ``name``; they are the two lookups behind the per-class view.

    Members whose oid is unknown, or whose user has no ``class_id``, collapse
    into one class-less row (``group_oid`` / ``group_name`` both empty); a
    ``class_id`` with no matching class keeps its oid with an empty name. Both
    kinds share an empty ``group_name`` and are sorted last, the named classes
    ahead of them by ``sum`` descending. ``modes`` always follow
    :data:`MODE_ORDER`.

    :param members: Effective memberships of the activity
    :param users: Full user rows (for the user -> class mapping)
    :param classes: Class rows (for the class oid -> name mapping)
    :return: The six keys of the analysis contract; empty ``members`` yields
        :func:`empty_analysis_payload`
    """
    if not members:
        return empty_analysis_payload()
    durations = [float(member["duration"]) for member in members]
    bin_width = pick_bin_width(max(durations))

    modes = [
        {
            "mode": row["key"],
            "count": row["count"],
            "sum": row["sum"],
            "mean": row["mean"],
        }
        for row in group_durations(
            [(member["mode"], float(member["duration"])) for member in members],
            MODE_ORDER,
        )
    ]

    class_of_user = {str(user["_id"]): user.get("class_id") or "" for user in users}
    name_of_class = {str(item["_id"]): item.get("name") or "" for item in classes}
    grouped_classes = [
        {
            "group_oid": row["key"],
            "group_name": name_of_class.get(row["key"], "") if row["key"] else "",
            "count": row["count"],
            "sum": row["sum"],
            "mean": row["mean"],
        }
        for row in group_durations(
            [
                (
                    class_of_user.get(str(member["member"]), ""),
                    float(member["duration"]),
                )
                for member in members
            ]
        )
    ]
    grouped_classes.sort(
        key=lambda row: (row["group_name"] == "", -row["sum"], row["group_name"])
    )

    return {
        "total": len(durations),
        "bin_width": bin_width,
        "description": describe_durations(durations),
        "bins": build_bins(durations, bin_width),
        "modes": modes,
        "classes": grouped_classes,
    }


def to_mcp_statistics(description: dict) -> dict:
    """Map a :func:`describe_durations` result onto the MCP statistics contract.

    The MCP endpoint keeps its historical key names, which differ from the
    analysis ones; ``mode`` belongs to the description helper only and is
    deliberately not part of this payload.

    :param description: Result of :func:`describe_durations` (non-``None``)
    :return: ``total_members`` / ``mean_duration`` / ``median_duration`` /
        ``std_deviation`` / ``min_duration`` / ``max_duration`` /
        ``total_duration`` / ``variance`` / ``percentile_25`` / ``percentile_75``
    """
    return {
        "total_members": description["total"],
        "mean_duration": description["mean"],
        "median_duration": description["median"],
        "std_deviation": description["std"],
        "min_duration": description["min"],
        "max_duration": description["max"],
        "total_duration": description["sum"],
        "variance": description["var"],
        "percentile_25": description["25_percentile"],
        "percentile_75": description["75_percentile"],
    }
