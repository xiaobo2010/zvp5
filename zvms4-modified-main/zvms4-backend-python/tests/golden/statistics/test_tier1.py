"""Tier-1: offline unit tests for the pure-Python statistics helpers.

No database, no fixtures directory — just the hand-computed expectations of the
five helper functions in :mod:`tasks.compute_time` that the set-based rebuild uses
to assemble rows in Python:

    _adjusted_frame / _grade_rollups / _group_rollup_rows /
    _origin_user_rows / _origin_stat_rows

Run with pytest (repo root on ``sys.path`` via ``conftest.py``)::

    pytest tests/golden/statistics/test_tier1.py -q
"""

from __future__ import annotations

import pandas as pd
import pytest

from config import BASE_OFF_CAMPUS, BASE_ON_CAMPUS, BASE_SOCIAL_PRACTICE
from tasks.compute_time import (
    _adjusted_frame,
    _grade_rollups,
    _group_rollup_rows,
    _origin_stat_rows,
    _origin_user_rows,
)

# ---------------------------------------------------------------------------
# Hand-computed fixture: 5 users across 3 grades / 2 classes (+1 class-less user)
#
#   u1 grade 2023 class c1 raw (on 30, off 20, social 20) -> pct 100
#   u2 grade 2023 class c1 raw (on 10, off  5, social  0) -> pct 25.8620689655
#   u3 grade 2024 class c2 raw (on 50, off 30, social  0) -> pct 68.9655172414
#   u4 grade 2024 class None raw (0, 0, 100)              -> pct 31.0344827586
#   u5 grade 2022 class c2 raw (on 25, off 15, social 18) -> pct 100
# ---------------------------------------------------------------------------

_RAW_COLUMNS = [
    "user_oid",
    "grade",
    "class_oid",
    "on_campus_raw",
    "off_campus_raw",
    "social_practice",
]

_RAW_ROWS = [
    ("u1", "2023", "c1", 30.0, 20.0, 20.0),
    ("u2", "2023", "c1", 10.0, 5.0, 0.0),
    ("u3", "2024", "c2", 50.0, 30.0, 0.0),
    ("u4", "2024", None, 0.0, 0.0, 100.0),
    ("u5", "2022", "c2", 25.0, 15.0, 18.0),
]

# Folded hours (process_time_data, np.round(...,1) on the transferred part).
_FOLDED = {
    "u1": (32.5, 21.7, 20.0),
    "u2": (10.0, 5.0, 0.0),
    "u3": (57.5, 38.3, 0.0),
    "u4": (0.0, 0.0, 100.0),
    "u5": (25.0, 15.0, 18.0),
}

# Rebuild percentage (from the raw columns, matching typings.time.UserActivityTime).
_PERCENTAGE = {
    "u1": 100.0,
    "u2": 15.0 / 58.0 * 100.0,
    "u3": 40.0 / 58.0 * 100.0,
    "u4": 18.0 / 58.0 * 100.0,
    "u5": 100.0,
}


def _raw_frame() -> pd.DataFrame:
    return pd.DataFrame(_RAW_ROWS, columns=_RAW_COLUMNS)


def _totals() -> list[dict]:
    return [
        {
            "user_oid": row[0],
            "grade": row[1],
            "class_oid": row[2],
            "on_campus_raw": row[3],
            "off_campus_raw": row[4],
            "social_practice": row[5],
        }
        for row in _RAW_ROWS
    ]


def _origins() -> list[dict]:
    return [
        {"user_oid": "u1", "origin": "labor", "mode": "on-campus", "total": 4.0},
        {"user_oid": "u1", "origin": "labor", "mode": "off-campus", "total": 1.0},
        {"user_oid": "u1", "origin": "organization", "mode": "social-practice", "total": 3.0},
        {"user_oid": "u3", "origin": "tasks", "mode": "on-campus", "total": 2.0},
        {"user_oid": "u5", "origin": "club", "mode": "off-campus", "total": 5.0},
        # u2 / u4 deliberately have no origins (the "no origin" branch)
    ]


def _adjusted() -> pd.DataFrame:
    return _adjusted_frame(_raw_frame())


# ---------------------------------------------------------------------------
# _adjusted_frame
# ---------------------------------------------------------------------------

def test_adjusted_frame_empty_inputs() -> None:
    for empty in (None, pd.DataFrame()):
        frame = _adjusted_frame(empty)
        assert frame.empty
        assert list(frame.columns) == [
            "user_oid",
            "grade",
            "class_oid",
            "on-campus",
            "off-campus",
            "social-practice",
            "percentage",
        ]


def test_adjusted_frame_folded_hours_and_percentage() -> None:
    frame = _adjusted_frame(_raw_frame()).set_index("user_oid")
    for user, (on, off, social) in _FOLDED.items():
        assert frame.loc[user, "on-campus"] == pytest.approx(on, rel=1e-12)
        assert frame.loc[user, "off-campus"] == pytest.approx(off, rel=1e-12)
        assert frame.loc[user, "social-practice"] == pytest.approx(social, rel=1e-12)
    for user, pct in _PERCENTAGE.items():
        assert frame.loc[user, "percentage"] == pytest.approx(pct, rel=1e-12)


def test_adjusted_frame_keeps_null_class() -> None:
    frame = _adjusted_frame(_raw_frame()).set_index("user_oid")
    assert pd.isna(frame.loc["u4", "class_oid"])
    assert frame.loc["u1", "class_oid"] == "c1"


# ---------------------------------------------------------------------------
# _grade_rollups
# ---------------------------------------------------------------------------

def test_grade_rollups_empty() -> None:
    assert _grade_rollups(_adjusted_frame(None)) == ({}, {})
    assert _grade_rollups(None) == ({}, {})


def test_grade_rollups_keys() -> None:
    percentiles, indicators = _grade_rollups(_adjusted())
    assert set(percentiles) == {"2022", "2023", "2024"}
    assert set(indicators) == {"2022", "2023", "2024"}


def test_grade_rollups_single_member_percentiles_are_degenerate() -> None:
    # A one-user grade makes every percentile identical: they dedup to one key,
    # and the smallest percentile label ("0%") wins because we iterate 100->0.
    percentiles, _ = _grade_rollups(_adjusted())
    assert percentiles["2022"]["on-campus"] == {"0%": float(BASE_ON_CAMPUS)}
    assert percentiles["2022"]["off-campus"] == {"0%": float(BASE_OFF_CAMPUS)}
    assert percentiles["2022"]["social-practice"] == {"0%": float(BASE_SOCIAL_PRACTICE)}


def test_grade_rollups_hand_computed_indicators() -> None:
    _, indicators = _grade_rollups(_adjusted())
    # Grade 2022 = only u5 = the three base values.
    assert indicators["2022"]["on-campus"] == {
        "mean": 25.0,
        "median": 25.0,
        "mode": 25.0,
        "std": 0.0,
        "min": 25.0,
        "max": 25.0,
        "var": 0.0,
        "sum": 25.0,
    }
    # Grade 2023 = [u1, u2]; on-campus folded [32.5, 10.0].
    on_campus = indicators["2023"]["on-campus"]
    assert on_campus["mean"] == pytest.approx(21.25)
    assert on_campus["median"] == pytest.approx(21.25)
    assert on_campus["mode"] == pytest.approx(10.0)
    assert on_campus["std"] == pytest.approx(11.25)
    assert on_campus["min"] == pytest.approx(10.0)
    assert on_campus["max"] == pytest.approx(32.5)
    assert on_campus["var"] == pytest.approx(126.5625)
    assert on_campus["sum"] == pytest.approx(42.5)


# ---------------------------------------------------------------------------
# _group_rollup_rows
# ---------------------------------------------------------------------------

def test_group_rollup_rows_empty() -> None:
    assert _group_rollup_rows(_adjusted_frame(None)) == []
    assert _group_rollup_rows(None) == []


def test_group_rollup_rows_skips_classless_users() -> None:
    rows = _group_rollup_rows(_adjusted())
    by_group = {row["group_oid"]: row for row in rows}
    assert set(by_group) == {"c1", "c2"}  # u4 (class_oid NULL) produces no row
    assert "0" not in by_group and "nan" not in by_group
    assert by_group["c1"]["indicators"]["on-campus"]["mean"] == pytest.approx(21.25)
    # c1 on-campus folded [32.5, 10.0] bounded at 25: the clamped max keeps the
    # smallest label that reaches it (67%), the raw min keeps "0%".
    assert by_group["c1"]["percentiles"]["on-campus"]["67%"] == 25.0
    assert by_group["c1"]["percentiles"]["on-campus"]["0%"] == 10.0
    assert by_group["c1"]["percentiles"]["social-practice"]["0%"] == 0.0


def test_group_rollup_rows_shape() -> None:
    rows = _group_rollup_rows(_adjusted())
    for row in rows:
        assert set(row) == {"group_oid", "percentiles", "indicators"}
        assert set(row["percentiles"]) == {"on-campus", "off-campus", "social-practice"}
        assert set(row["indicators"]) == {"on-campus", "off-campus", "social-practice"}


# ---------------------------------------------------------------------------
# _origin_user_rows
# ---------------------------------------------------------------------------

def test_origin_user_rows_empty() -> None:
    assert _origin_user_rows([], []) == []


def test_origin_user_rows_one_row_per_user_and_no_origin_branch() -> None:
    rows = _origin_user_rows(_totals(), _origins())
    by_user = {row["user_oid"]: row for row in rows}
    assert set(by_user) == {"u1", "u2", "u3", "u4", "u5"}
    # u2 / u4 have no memberships -> empty maps, not a missing row.
    assert by_user["u2"]["data"] == {"by_origin": {}, "percentage_by_origin": {}}
    assert by_user["u4"]["data"] == {"by_origin": {}, "percentage_by_origin": {}}
    # u1: labor 4+1=5, organization 3, total 8.
    u1 = by_user["u1"]["data"]
    assert u1["by_origin"] == {
        "labor": {"on-campus": 4.0, "off-campus": 1.0, "social-practice": 0.0},
        "organization": {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 3.0},
    }
    assert u1["percentage_by_origin"] == {"labor": 62.5, "organization": 37.5}


# ---------------------------------------------------------------------------
# _origin_stat_rows
# ---------------------------------------------------------------------------

def test_origin_stat_rows_empty() -> None:
    assert _origin_stat_rows([], [], _adjusted_frame(None)) == []


def test_origin_stat_rows_scopes_and_counts() -> None:
    rows = _origin_stat_rows(_totals(), _origins(), _adjusted())
    keys = {row["stat_key"] for row in rows}
    # 3 grades + 2 groups = 5 scopes x 4 require_percentage values = 20 rows.
    assert len(rows) == 20
    assert "grade_2023_0" in keys and "grade_2023_60" in keys
    assert "group_c1_0" in keys and "group_c2_80" in keys
    by_key = {row["stat_key"]: row for row in rows}
    assert by_key["group_c2_80"]["scope"] == "group"
    assert by_key["grade_2024_80"]["require_percentage"] == 80


def test_origin_stat_rows_selects_all_at_zero() -> None:
    by_key = {r["stat_key"]: r for r in _origin_stat_rows(_totals(), _origins(), _adjusted())}
    assert by_key["grade_2023_0"]["data"]["by_origin"] == {
        "labor": {"on-campus": 4.0, "off-campus": 1.0, "social-practice": 0.0},
        "organization": {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 3.0},
    }


def test_origin_stat_rows_filters_by_percentage() -> None:
    by_key = {r["stat_key"]: r for r in _origin_stat_rows(_totals(), _origins(), _adjusted())}
    # grade 2023 @60 -> only u1 (u2 = 25.86%); same data as u1's own row.
    assert by_key["grade_2023_60"]["data"]["by_origin"]["labor"]["on-campus"] == 4.0
    assert "organization" in by_key["grade_2023_60"]["data"]["by_origin"]
    # grade 2024 @60 -> only u3 (u4 = 31.03%).
    assert by_key["grade_2024_60"]["data"]["by_origin"] == {
        "tasks": {"on-campus": 2.0, "off-campus": 0.0, "social-practice": 0.0}
    }
    # group c2 @80 -> only u5 (u3 = 68.97%).
    assert by_key["group_c2_80"]["data"]["by_origin"] == {
        "club": {"on-campus": 0.0, "off-campus": 5.0, "social-practice": 0.0}
    }


def test_origin_stat_rows_all_filtered_out_is_empty_not_missing() -> None:
    by_key = {r["stat_key"]: r for r in _origin_stat_rows(_totals(), _origins(), _adjusted())}
    # grade 2024 @80 -> u3 (68.97%) and u4 (31.03%) both drop -> empty maps.
    assert by_key["grade_2024_80"]["data"] == {
        "by_origin": {},
        "percentage_by_origin": {},
    }
