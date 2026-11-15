"""Compare two frozen-format statistics dumps.

Rules (see the harness README for the rationale):

* floats compare with ``math.isclose(rel_tol=1e-9)`` — Oracle ``SUM`` reorders
  additions versus pandas' serial sum, so the two paths differ at the ulp level
  and a bit-exact comparison would be a false alarm;
* integer/float mixes are treated numerically; ``bool`` and ``str`` compare
  exactly (a JSON string that *looks* numeric is never coerced);
* dict key sets compare **exactly** (so percentile maps — ``round(x, 1)`` value
  labels mapped to percentile labels — must have identical key sets and values);
* list tables are first aligned by their business key (never by ``oid`` or list
  position), then compared field by field.

Every difference is returned as a structured record::

    {"table": "user_time", "path": "user_time[(u1,)].on_campus_raw",
     "kind": "value", "left": 3.0, "right": 3.0000000001}

``kind`` is ``only_left`` / ``only_right`` / ``value``. An empty list means the
two dumps are equivalent.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

#: Business key per list table (matches the frozen dump sort order).
LIST_KEYS: dict[str, tuple[str, ...]] = {
    "user_time": ("user_oid",),
    "time_academic_year": ("user_oid", "academic_year"),
    "time_with_origin": ("user_oid",),
    "time_with_origin_stat": ("stat_key",),
    "group_indicators": ("group_oid",),
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _norm_key(value: Any) -> Any:
    """Normalise a key component so int/float key types line up."""
    if _is_number(value):
        return ("num", float(value))
    return value


def _key_of(table: str, row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(_norm_key(row.get(k)) for k in LIST_KEYS[table])


def _numbers_equal(left: Any, right: Any, rel_tol: float) -> bool:
    if not (_is_number(left) and _is_number(right)):
        return False
    return math.isclose(float(left), float(right), rel_tol=rel_tol, abs_tol=0.0)


def _add(diffs: list[dict[str, Any]], table: str, path: str, kind: str,
         left: Any, right: Any) -> None:
    diffs.append(
        {"table": table, "path": path, "kind": kind, "left": left, "right": right}
    )


def _compare_value(
    table: str, path: str, left: Any, right: Any,
    rel_tol: float, diffs: list[dict[str, Any]],
) -> None:
    if isinstance(left, dict) or isinstance(right, dict):
        if not (isinstance(left, dict) and isinstance(right, dict)):
            _add(diffs, table, path, "value", left, right)
            return
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left:
                _add(diffs, table, child, "only_right", None, right[key])
            elif key not in right:
                _add(diffs, table, child, "only_left", left[key], None)
            else:
                _compare_value(table, child, left[key], right[key], rel_tol, diffs)
        return

    if isinstance(left, list) or isinstance(right, list):
        if not (isinstance(left, list) and isinstance(right, list)):
            _add(diffs, table, path, "value", left, right)
            return
        if len(left) != len(right):
            _add(diffs, table, f"{path}.length", "value", len(left), len(right))
        for index, (l_item, r_item) in enumerate(zip(left, right)):
            _compare_value(table, f"{path}[{index}]", l_item, r_item, rel_tol, diffs)
        return

    if _is_number(left) or _is_number(right):
        if not _numbers_equal(left, right, rel_tol):
            _add(diffs, table, path, "value", left, right)
        return

    if left != right:
        _add(diffs, table, path, "value", left, right)


def _compare_list(
    table: str, left: list[dict[str, Any]], right: list[dict[str, Any]],
    rel_tol: float, diffs: list[dict[str, Any]],
) -> None:
    left_index = {_key_of(table, row): row for row in left}
    right_index = {_key_of(table, row): row for row in right}
    for key in sorted(set(left_index) | set(right_index), key=repr):
        label = f"{table}{list(key)}"
        if key not in left_index:
            _add(diffs, table, label, "only_right", None, right_index[key])
        elif key not in right_index:
            _add(diffs, table, label, "only_left", left_index[key], None)
        else:
            _compare_value(
                table, label, left_index[key], right_index[key], rel_tol, diffs
            )


def compare(
    left: dict[str, Any], right: dict[str, Any], *, rel_tol: float = 1e-9
) -> list[dict[str, Any]]:
    """Return the structured differences between two dumps (empty = equal)."""
    diffs: list[dict[str, Any]] = []
    for table in LIST_KEYS:
        _compare_list(
            table, left.get(table, []), right.get(table, []), rel_tol, diffs
        )
    _compare_value(
        "indicators", "indicators.percentiles",
        (left.get("indicators") or {}).get("percentiles"),
        (right.get("indicators") or {}).get("percentiles"),
        rel_tol, diffs,
    )
    _compare_value(
        "indicators", "indicators.indicators",
        (left.get("indicators") or {}).get("indicators"),
        (right.get("indicators") or {}).get("indicators"),
        rel_tol, diffs,
    )
    return diffs


def format_diff(diff: dict[str, Any]) -> str:
    kind = diff["kind"]
    if kind == "value":
        return f"[{diff['table']}] {diff['path']}: {diff['left']!r} != {diff['right']!r}"
    marker = "only in LEFT (reference)" if kind == "only_left" else "only in RIGHT (rebuild)"
    payload = diff["left"] if kind == "only_left" else diff["right"]
    return f"[{diff['table']}] {diff['path']}: {marker}: {payload!r}"


def summarise(diffs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diff in diffs:
        counts[diff["table"]] = counts.get(diff["table"], 0) + 1
    return counts


def compare_files(left_path: Path | str, right_path: Path | str,
                  *, rel_tol: float = 1e-9) -> list[dict[str, Any]]:
    left = json.loads(Path(left_path).read_text(encoding="utf-8"))
    right = json.loads(Path(right_path).read_text(encoding="utf-8"))
    return compare(left, right, rel_tol=rel_tol)


def _main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: compare.py <left.json> <right.json>", file=sys.stderr)
        return 2
    diffs = compare_files(argv[1], argv[2])
    if not diffs:
        print("IDENTICAL")
        return 0
    for diff in diffs:
        print(format_diff(diff))
    print(f"{len(diffs)} difference(s): {summarise(diffs)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
