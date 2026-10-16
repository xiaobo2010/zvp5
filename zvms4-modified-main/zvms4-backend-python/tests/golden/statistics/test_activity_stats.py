"""Offline unit tests for the pure helpers behind the activity statistics endpoints.

No database, no fixtures directory, no I/O beyond ``conftest.py``'s ``sys.path``
setup — just the hand-computed expectations of the helpers in
:mod:`util.activity_statistics` that the statistics routers use:

    pick_bin_width / build_bins / describe_durations / group_durations /
    empty_analysis_payload / build_analysis_payload / to_mcp_statistics

Run with pytest (repo root on ``sys.path`` via ``conftest.py``)::

    pytest tests/golden/statistics/test_activity_stats.py -q
"""

from __future__ import annotations

import math

import pytest

from util.activity_statistics import (
    BIN_WIDTH_LARGE,
    BIN_WIDTH_SMALL,
    MAX_BINS,
    MODE_ORDER,
    build_analysis_payload,
    build_bins,
    describe_durations,
    empty_analysis_payload,
    group_durations,
    pick_bin_width,
    to_mcp_statistics,
)

# ---------------------------------------------------------------------------
# Hand-computed fixture: an activity's effective durations, in hours
#
#   sample A = [1.0, 2.0, 2.0, 3.0, 5.0]  -> repeated 2.0, percentile on index
#   sample B = [0.0, 1.0, 2.0, 3.0]        -> all unique, percentile interpolated
# ---------------------------------------------------------------------------

_SAMPLE_A = [1.0, 2.0, 2.0, 3.0, 5.0]
_SAMPLE_B = [0.0, 1.0, 2.0, 3.0]

_DESCRIPTION_KEYS = {
    "mean",
    "median",
    "mode",
    "std",
    "min",
    "max",
    "var",
    "sum",
    "25_percentile",
    "75_percentile",
    "total",
}


# ---------------------------------------------------------------------------
# pick_bin_width
# ---------------------------------------------------------------------------

def test_pick_bin_width_threshold_is_inclusive() -> None:
    # 10 h and below -> half-hour bins; strictly above -> whole-hour bins.
    assert pick_bin_width(10.0) == BIN_WIDTH_SMALL
    assert pick_bin_width(10.1) == BIN_WIDTH_LARGE


def test_pick_bin_width_small_values() -> None:
    assert pick_bin_width(0.0) == BIN_WIDTH_SMALL
    assert pick_bin_width(9.99) == BIN_WIDTH_SMALL
    assert pick_bin_width(12.0) == BIN_WIDTH_LARGE


def test_pick_bin_width_keeps_normal_magnitudes_on_the_plain_rule() -> None:
    # Normal volunteer-time magnitudes never reach the cap: the plain rule holds
    # up to 999 h (floor(999 / 1.0) + 1 == MAX_BINS == 1000), so the first value
    # that widens is 1000.0 h, which would need 1001 bins.
    assert pick_bin_width(0.0) == BIN_WIDTH_SMALL
    assert pick_bin_width(10.0) == BIN_WIDTH_SMALL
    assert pick_bin_width(200.0) == BIN_WIDTH_LARGE
    assert pick_bin_width(999.0) == BIN_WIDTH_LARGE
    assert pick_bin_width(1000.0) == 2 * BIN_WIDTH_LARGE


def test_pick_bin_width_bounds_the_bin_count_on_dirty_data() -> None:
    # A corrupt duration (v2 hands us a bare float) must not ask for 1e9 bins.
    width = pick_bin_width(1e9)
    assert width > BIN_WIDTH_LARGE
    assert math.floor(1e9 / width) + 1 <= MAX_BINS
    assert len(build_bins([0.0, 1e9], width)) <= MAX_BINS


# ---------------------------------------------------------------------------
# build_bins
# ---------------------------------------------------------------------------

def test_build_bins_empty_input() -> None:
    assert build_bins([], BIN_WIDTH_SMALL) == []
    assert build_bins([], BIN_WIDTH_LARGE) == []


def test_build_bins_half_hour_bounds_and_counts() -> None:
    bins = build_bins([0.0, 0.4, 1.0, 1.5, 2.0], BIN_WIDTH_SMALL)
    # max 2.0 -> floor(2.0 / 0.5) + 1 = 5 bins, from 0.0 up to [2.0, 2.5).
    assert bins == [
        {"from": 0.0, "to": 0.5, "count": 2},
        {"from": 0.5, "to": 1.0, "count": 0},
        {"from": 1.0, "to": 1.5, "count": 1},
        {"from": 1.5, "to": 2.0, "count": 1},
        {"from": 2.0, "to": 2.5, "count": 1},
    ]


def test_build_bins_max_lands_in_last_bin() -> None:
    bins = build_bins([0.0, 0.5, 9.5, 10.0], BIN_WIDTH_LARGE)
    assert len(bins) == 11  # floor(10.0) + 1
    assert bins[-1] == {"from": 10.0, "to": 11.0, "count": 1}
    assert bins[0]["count"] == 2  # 0.0 and 0.5
    assert bins[9]["count"] == 1  # 9.5


@pytest.mark.parametrize(
    ("durations", "width"),
    [
        ([0.0, 0.4, 1.0, 1.5, 2.0], BIN_WIDTH_SMALL),
        ([0.0, 0.5, 9.5, 10.0], BIN_WIDTH_LARGE),
        ([2.0, 2.0, 2.0], BIN_WIDTH_SMALL),
        ([7.5], BIN_WIDTH_LARGE),
    ],
)
def test_build_bins_counts_sum_to_sample_size(durations, width) -> None:
    bins = build_bins(durations, width)
    assert sum(bin_["count"] for bin_ in bins) == len(durations)
    assert bins[-1]["from"] < bins[-1]["to"]


def test_build_bins_clamps_negative_durations_into_the_first_bin() -> None:
    # -0.5 // 0.5 == -1.0 -> the raw index is -1, which used to land in the last
    # bin through Python's negative indexing.
    bins = build_bins([-0.5, 1.0, 2.0], BIN_WIDTH_SMALL)
    assert len(bins) == 5
    assert bins[0] == {"from": 0.0, "to": 0.5, "count": 1}
    assert bins[2] == {"from": 1.0, "to": 1.5, "count": 1}
    assert bins[4] == {"from": 2.0, "to": 2.5, "count": 1}
    assert sum(bin_["count"] for bin_ in bins) == 3


def test_build_bins_all_negative_sample_still_gets_one_bin() -> None:
    # floor(max / width) + 1 would be 0 here; the histogram keeps one bin
    # instead of returning an empty list and indexing into nothing.
    assert build_bins([-3.0, -0.5], BIN_WIDTH_SMALL) == [
        {"from": 0.0, "to": 0.5, "count": 2}
    ]


# ---------------------------------------------------------------------------
# describe_durations
# ---------------------------------------------------------------------------

def test_describe_durations_empty_input() -> None:
    assert describe_durations([]) is None


def test_describe_durations_hand_computed() -> None:
    result = describe_durations(_SAMPLE_A)
    assert result is not None
    assert set(result) == _DESCRIPTION_KEYS
    assert result["mean"] == pytest.approx(2.6)
    assert result["median"] == pytest.approx(2.0)
    assert result["mode"] == pytest.approx(2.0)
    assert result["std"] == pytest.approx(1.3564659966250538)
    assert result["min"] == pytest.approx(1.0)
    assert result["max"] == pytest.approx(5.0)
    assert result["var"] == pytest.approx(1.84)
    assert result["sum"] == pytest.approx(13.0)
    assert result["25_percentile"] == pytest.approx(2.0)
    assert result["75_percentile"] == pytest.approx(3.0)
    assert result["total"] == 5
    assert type(result["total"]) is int


def test_describe_durations_interpolated_percentiles() -> None:
    result = describe_durations(_SAMPLE_B)
    assert result is not None
    # p25 sits at index 0.75 -> 0 + 0.75 * (1 - 0); p75 at 2.25 -> 2 + 0.25 * 1.
    assert result["mean"] == pytest.approx(1.5)
    assert result["median"] == pytest.approx(1.5)
    assert result["std"] == pytest.approx(1.118033988749895)
    assert result["var"] == pytest.approx(1.25)
    assert result["sum"] == pytest.approx(6.0)
    assert result["25_percentile"] == pytest.approx(0.75)
    assert result["75_percentile"] == pytest.approx(2.25)
    assert result["total"] == 4


def test_describe_durations_all_unique_mode_is_the_minimum() -> None:
    result = describe_durations(_SAMPLE_B)
    assert result is not None
    assert result["mode"] == pytest.approx(min(_SAMPLE_B))


def test_describe_durations_returns_plain_scalars() -> None:
    result = describe_durations(_SAMPLE_A)
    assert result is not None
    for key in _DESCRIPTION_KEYS - {"total"}:
        assert type(result[key]) is float, key
    assert type(result["total"]) is int


# ---------------------------------------------------------------------------
# group_durations
# ---------------------------------------------------------------------------

def test_group_durations_empty_input() -> None:
    assert group_durations([]) == []
    assert group_durations([], ["on-campus", "off-campus"]) == []


def test_group_durations_follows_the_given_order() -> None:
    entries = [
        ("on-campus", 2.0),
        ("on-campus", 3.0),
        ("off-campus", 1.0),
        ("social-practice", 4.0),
        ("legacy", 5.0),
    ]
    rows = group_durations(entries, ["on-campus", "off-campus", "social-practice"])
    # The declared order wins even though "legacy" has the largest sum.
    assert [row["key"] for row in rows] == [
        "on-campus",
        "off-campus",
        "social-practice",
        "legacy",
    ]
    assert rows[0] == {"key": "on-campus", "count": 2, "sum": 5.0, "mean": 2.5}
    assert rows[1] == {"key": "off-campus", "count": 1, "sum": 1.0, "mean": 1.0}
    assert rows[2] == {"key": "social-practice", "count": 1, "sum": 4.0, "mean": 4.0}
    assert rows[3] == {"key": "legacy", "count": 1, "sum": 5.0, "mean": 5.0}


def test_group_durations_omits_ordered_keys_that_are_absent() -> None:
    rows = group_durations(
        [("off-campus", 1.0), ("on-campus", 3.0)],
        ["on-campus", "off-campus", "social-practice"],
    )
    assert [row["key"] for row in rows] == ["on-campus", "off-campus"]


def test_group_durations_appends_unordered_keys_ascending() -> None:
    entries = [
        ("on-campus", 1.0),
        ("zulu", 9.0),
        ("alpha", 9.0),
        ("off-campus", 1.0),
    ]
    rows = group_durations(entries, ["on-campus", "off-campus"])
    # Unordered keys come last, sorted by key ascending.
    assert [row["key"] for row in rows] == [
        "on-campus",
        "off-campus",
        "alpha",
        "zulu",
    ]


def test_group_durations_without_order_sorts_by_sum_descending() -> None:
    entries = [("b", 3.0), ("a", 5.0), ("c", 5.0), ("b", 1.0)]
    rows = group_durations(entries)
    # a and c tie on 5.0 -> key ascending; b (4.0) last.
    assert [row["key"] for row in rows] == ["a", "c", "b"]
    assert [row["sum"] for row in rows] == pytest.approx([5.0, 5.0, 4.0])
    assert rows[2] == {"key": "b", "count": 2, "sum": 4.0, "mean": 2.0}


# ---------------------------------------------------------------------------
# build_analysis_payload / empty_analysis_payload / to_mcp_statistics
#
# Rows as the router reads them, deliberately without any ``status`` key: the
# effective-only filter belongs to the caller, the pure function must not touch
# it.
#
#   u1 -> class c1 (named)   u2 -> class c1 (named)   u3 -> class c9 (no name)
#   u4 -> no class_id        ghost -> not in users at all
# ---------------------------------------------------------------------------

_MEMBERS = [
    {"member": "u1", "mode": "on-campus", "duration": 1.0},
    {"member": "u2", "mode": "off-campus", "duration": 2.0},
    {"member": "u3", "mode": "social-practice", "duration": 3.0},
    {"member": "u4", "mode": "on-campus", "duration": 4.0},
    {"member": "ghost", "mode": "hybrid", "duration": 5.0},
]

_USERS = [
    {"_id": "u1", "class_id": "c1"},
    {"_id": "u2", "class_id": "c1"},
    {"_id": "u3", "class_id": "c9"},
    {"_id": "u4"},
]

_CLASSES = [{"_id": "c1", "name": "高一(1)班"}]

_ANALYSIS_KEYS = {"total", "bin_width", "description", "bins", "modes", "classes"}

_MCP_KEYS = {
    "total_members",
    "mean_duration",
    "median_duration",
    "std_deviation",
    "min_duration",
    "max_duration",
    "total_duration",
    "variance",
    "percentile_25",
    "percentile_75",
}


def test_empty_analysis_payload_shape_and_freshness() -> None:
    first = empty_analysis_payload()
    second = empty_analysis_payload()
    assert set(first) == _ANALYSIS_KEYS
    assert first == {
        "total": 0,
        "bin_width": BIN_WIDTH_SMALL,
        "description": None,
        "bins": [],
        "modes": [],
        "classes": [],
    }
    # A new object (and new lists) every call, never shared response state.
    assert first is not second
    assert first["bins"] is not second["bins"]
    assert first["modes"] is not second["modes"]
    assert first["classes"] is not second["classes"]


def test_build_analysis_payload_empty_members_is_the_empty_payload() -> None:
    assert build_analysis_payload([], _USERS, _CLASSES) == empty_analysis_payload()


def test_build_analysis_payload_hand_computed() -> None:
    payload = build_analysis_payload(_MEMBERS, _USERS, _CLASSES)
    assert set(payload) == _ANALYSIS_KEYS
    # Durations [1, 2, 3, 4, 5]: max 5 h -> 0.5 h bins, 11 of them.
    assert payload["total"] == 5
    assert payload["bin_width"] == BIN_WIDTH_SMALL
    assert payload["bins"] == [
        {"from": 0.0, "to": 0.5, "count": 0},
        {"from": 0.5, "to": 1.0, "count": 0},
        {"from": 1.0, "to": 1.5, "count": 1},
        {"from": 1.5, "to": 2.0, "count": 0},
        {"from": 2.0, "to": 2.5, "count": 1},
        {"from": 2.5, "to": 3.0, "count": 0},
        {"from": 3.0, "to": 3.5, "count": 1},
        {"from": 3.5, "to": 4.0, "count": 0},
        {"from": 4.0, "to": 4.5, "count": 1},
        {"from": 4.5, "to": 5.0, "count": 0},
        {"from": 5.0, "to": 5.5, "count": 1},
    ]
    description = payload["description"]
    assert description is not None
    assert set(description) == _DESCRIPTION_KEYS
    assert description["mean"] == pytest.approx(3.0)
    assert description["median"] == pytest.approx(3.0)
    assert description["mode"] == pytest.approx(1.0)  # all unique -> the minimum
    assert description["std"] == pytest.approx(1.4142135623730951)
    assert description["var"] == pytest.approx(2.0)
    assert description["sum"] == pytest.approx(15.0)
    assert description["25_percentile"] == pytest.approx(2.0)
    assert description["75_percentile"] == pytest.approx(4.0)
    assert description["total"] == 5
    # on-campus 1+4=5, off-campus 2, social-practice 3, unknown "hybrid" appended.
    assert payload["modes"] == [
        {"mode": "on-campus", "count": 2, "sum": 5.0, "mean": 2.5},
        {"mode": "off-campus", "count": 1, "sum": 2.0, "mean": 2.0},
        {"mode": "social-practice", "count": 1, "sum": 3.0, "mean": 3.0},
        {"mode": "hybrid", "count": 1, "sum": 5.0, "mean": 5.0},
    ]
    # c1 (named, sum 3) then the two empty-name rows by sum desc: the class-less
    # pair (sum 9) before c9 (sum 3), which keeps its oid with an empty name.
    assert payload["classes"] == [
        {"group_oid": "c1", "group_name": "高一(1)班", "count": 2, "sum": 3.0, "mean": 1.5},
        {"group_oid": "", "group_name": "", "count": 2, "sum": 9.0, "mean": 4.5},
        {"group_oid": "c9", "group_name": "", "count": 1, "sum": 3.0, "mean": 3.0},
    ]


def test_build_analysis_payload_modes_follow_the_mode_order() -> None:
    members = [
        {"member": "u1", "mode": mode, "duration": 1.0}
        for mode in ["hybrid", "social-practice", "off-campus", "on-campus"]
    ]
    payload = build_analysis_payload(members, _USERS, _CLASSES)
    assert [row["mode"] for row in payload["modes"]] == [*MODE_ORDER, "hybrid"]


def test_build_analysis_payload_classless_rows_sort_last_despite_their_sum() -> None:
    members = [
        {"member": "u1", "mode": "on-campus", "duration": 1.0},  # class c1
        {"member": "u4", "mode": "on-campus", "duration": 9.0},  # no class_id
        {"member": "ghost", "mode": "on-campus", "duration": 9.0},  # not in users
    ]
    payload = build_analysis_payload(members, _USERS, _CLASSES)
    assert payload["classes"] == [
        {"group_oid": "c1", "group_name": "高一(1)班", "count": 1, "sum": 1.0, "mean": 1.0},
        {"group_oid": "", "group_name": "", "count": 2, "sum": 18.0, "mean": 9.0},
    ]


def test_build_analysis_payload_unresolved_class_name_keeps_its_oid() -> None:
    members = [
        {"member": "u1", "mode": "on-campus", "duration": 1.0},  # class c1, named
        {"member": "u3", "mode": "on-campus", "duration": 9.0},  # class c9, unnamed
    ]
    payload = build_analysis_payload(members, _USERS, _CLASSES)
    assert payload["classes"] == [
        {"group_oid": "c1", "group_name": "高一(1)班", "count": 1, "sum": 1.0, "mean": 1.0},
        {"group_oid": "c9", "group_name": "", "count": 1, "sum": 9.0, "mean": 9.0},
    ]


# ---------------------------------------------------------------------------
# to_mcp_statistics
# ---------------------------------------------------------------------------

def test_to_mcp_statistics_key_set_is_exactly_the_ten_mcp_keys() -> None:
    description = describe_durations(_SAMPLE_A)
    assert description is not None
    result = to_mcp_statistics(description)
    assert set(result) == _MCP_KEYS
    assert "mode" not in result
    assert "sum" not in result
    assert "total" not in result


def test_to_mcp_statistics_maps_every_value() -> None:
    description = describe_durations(_SAMPLE_A)
    assert description is not None
    result = to_mcp_statistics(description)
    assert result["total_members"] == description["total"] == 5
    assert result["mean_duration"] == pytest.approx(2.6)
    assert result["median_duration"] == pytest.approx(2.0)
    assert result["std_deviation"] == pytest.approx(1.3564659966250538)
    assert result["min_duration"] == pytest.approx(1.0)
    assert result["max_duration"] == pytest.approx(5.0)
    assert result["total_duration"] == pytest.approx(13.0)
    assert result["variance"] == pytest.approx(1.84)
    assert result["percentile_25"] == pytest.approx(2.0)
    assert result["percentile_75"] == pytest.approx(3.0)

