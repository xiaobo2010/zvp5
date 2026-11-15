"""Offline drift check: ``db/models.py`` (SQLAlchemy Core) vs ``V001`` DDL.

Run with::

    python -m db.ddl_check

It compiles every :class:`~sqlalchemy.Table` in :data:`db.models.metadata` with
the Oracle dialect, parses those ``CREATE TABLE`` / ``CREATE INDEX`` statements
and the statements embedded in ``db/oracle/ddl/V001__initial_schema.sql`` into
a normalized structural form, and compares them. It performs **no database
I/O**.

Normalization rules
-------------------
Rather than compare raw DDL strings (which are brittle: column order, inline
vs. table-level constraints, auto-generated constraint names, and the Oracle
dialect's ``VARCHAR2(n CHAR)`` length semantics all differ textually), both
sides are parsed into the same canonical structure and compared:

* **case** - everything is lower-cased.
* **whitespace** - runs of whitespace collapse to a single space (and are
  removed entirely inside type names).
* **CHAR length semantics** - ``varchar2(24 char)`` is folded to
  ``varchar2(24)``; the ``CHAR`` modifier is a length-semantics detail, not a
  logical difference.
* **NUMBER vs NUMERIC** - ``numeric(k)`` is folded to ``number(k)``.
* **column order** - ignored (columns are compared as a mapping).
* **constraint / index order** - ignored (compared as sets).
* **constraint name** and ``ON DELETE`` clause - ignored (we compare logical
  presence: PK / UQ / FK(ref'd table+cols) / CHECK(expr)).
* **indexes** - compared by ``(table, is_unique, columns)``, ignoring names.

Anything left over (missing/extra column, type/nullability/default mismatch,
or a missing/extra PK/UQ/FK/CHECK/index) is reported per table with a readable
diff and makes the process exit non-zero.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from sqlalchemy.dialects import oracle
from sqlalchemy.schema import CreateIndex, CreateTable

from db import models

DDL_PATH = Path(__file__).resolve().parent / "oracle" / "ddl" / "V001__initial_schema.sql"

_WS = re.compile(r"\s+")
_CREATE_TABLE = re.compile(r"create\s+table\s+([a-z0-9_]+)\s*\(", re.IGNORECASE)
_CREATE_INDEX = re.compile(
    r"create\s+(unique\s+)?index\s+[a-z0-9_]+\s+on\s+([a-z0-9_]+)\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_EXECUTE_IMMEDIATE = re.compile(
    r"execute\s+immediate\s+'((?:[^']|'')*)'", re.IGNORECASE | re.DOTALL
)


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def extract_execute_immediate(sql_text: str) -> str:
    """Return the concatenated DDL embedded in ``EXECUTE IMMEDIATE '...';``.

    Doubled single quotes (``''``) are un-escaped back to ``'`` so the result
    is parseable as plain SQL.
    """
    parts = [m.group(1).replace("''", "'") for m in _EXECUTE_IMMEDIATE.finditer(sql_text)]
    return "\n".join(parts)


def _collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _split_top_level(body: str) -> List[str]:
    """Split a comma list at paren depth 0, honoring single quotes."""
    parts: List[str] = []
    cur: List[str] = []
    depth = 0
    in_quote = False
    for ch in body:
        if ch == "'":
            in_quote = not in_quote
        if not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur))
                cur = []
                continue
        cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _match_paren(text: str, open_idx: int) -> int:
    """Return index of the ``)`` matching the ``(`` at ``open_idx``."""
    depth = 0
    in_quote = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "'":
            in_quote = not in_quote
        if not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
    raise ValueError("unbalanced parentheses in DDL")


def _norm_type(type_str: str) -> str:
    t = type_str.lower()
    t = re.sub(r"\((\d+)\s+char\)", r"(\1)", t)  # varchar2(n char) -> varchar2(n)
    t = _WS.sub("", t)
    t = t.replace("numeric(", "number(")
    return t


def _norm_expr(expr: str) -> str:
    return _collapse(expr.lower())


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _parse_column(frag: str) -> Tuple[str, str, bool, str | None]:
    frag = _collapse(frag)
    # 标识符的两侧引号要吃掉：Oracle 的保留字（``MODE`` …）只能带引号出现，
    # 而引号是引用方式、不是列名的一部分——把它留在名字里会让 models 侧
    # ``"MODE"`` 与 V001 侧的同名列永远对不上。
    m = re.match(r'^"?([a-z0-9_]+)"?\s+(.*)$', frag, re.IGNORECASE)
    if not m:
        raise ValueError(f"unparseable column: {frag!r}")
    name, rest = m.group(1).lower(), m.group(2)
    rest_l = rest.lower()
    notnull = "not null" in rest_l
    rest_no_nn = re.sub(r"\bnot null\b", "", rest, flags=re.IGNORECASE)
    dm = re.search(r"\bdefault\b\s*(.+)$", rest_no_nn, re.IGNORECASE)
    if dm:
        default = _collapse(dm.group(1))
        type_part = rest_no_nn[: dm.start()]
    else:
        default = None
        type_part = rest_no_nn
    return name, _norm_type(type_part), notnull, default


def _parse_constraint(frag: str):
    f = _collapse(frag).lower()
    m = re.match(r"^(?:constraint\s+[a-z0-9_]+\s+)?primary key\s*\(([^)]*)\)$", f)
    if m:
        return ("pk", tuple(c.strip() for c in m.group(1).split(",")))
    m = re.match(r"^(?:constraint\s+[a-z0-9_]+\s+)?unique\s*\(([^)]*)\)$", f)
    if m:
        return ("uq", tuple(c.strip() for c in m.group(1).split(",")))
    m = re.match(
        r"^(?:constraint\s+[a-z0-9_]+\s+)?foreign key\s*\(([^)]*)\)\s*"
        r"references\s+([a-z0-9_]+)\s*\(([^)]*)\)",
        f,
    )
    if m:
        cols = tuple(c.strip() for c in m.group(1).split(","))
        ref = (m.group(2), tuple(c.strip() for c in m.group(3).split(",")))
        return ("fk", cols, ref)
    m = re.match(r"^(?:constraint\s+[a-z0-9_]+\s+)?check\s*\((.*)\)$", f)
    if m:
        return ("ck", _norm_expr(m.group(1)))
    raise ValueError(f"unrecognized constraint: {frag!r}")


def parse_schema(text: str) -> Dict:
    """Parse plain DDL text into ``{tables, indexes}`` canonical structures."""
    tables: Dict[str, Dict] = {}
    indexes = set()

    for m in _CREATE_TABLE.finditer(text):
        name = m.group(1).lower()
        body = text[m.end() - 1 : _match_paren(text, m.end() - 1) + 1][1:-1]
        columns: Dict[str, tuple] = {}
        pk = None
        uqs = set()
        fks = set()
        cks = set()
        for frag in _split_top_level(body):
            head = _collapse(frag).lower()
            if re.match(
                r"^(constraint\s+\w+\s+)?(primary key|unique|foreign key|check)\b",
                head,
            ):
                kind = _parse_constraint(frag)
                if kind[0] == "pk":
                    pk = kind[1]
                elif kind[0] == "uq":
                    uqs.add(kind[1])
                elif kind[0] == "fk":
                    fks.add((kind[1], kind[2]))
                elif kind[0] == "ck":
                    cks.add(kind[1])
            else:
                col = _parse_column(frag)
                columns[col[0]] = (col[1], col[2], col[3])
        tables[name] = {
            "columns": columns,
            "pk": pk,
            "uq": uqs,
            "fk": fks,
            "ck": cks,
        }

    for m in _CREATE_INDEX.finditer(text):
        is_unique = bool(m.group(1))
        table = m.group(2).lower()
        cols = tuple(c.strip() for c in m.group(3).split(","))
        indexes.add((table, is_unique, cols))

    return {"tables": tables, "indexes": indexes}


def parse_v001(path: Path = DDL_PATH) -> Dict:
    return parse_schema(extract_execute_immediate(path.read_text(encoding="utf-8")))


def parse_models() -> Dict:
    dialect = oracle.dialect()
    stmts: List[str] = []
    for table in models.metadata.tables.values():
        stmts.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        for index in table.indexes:
            stmts.append(str(CreateIndex(index).compile(dialect=dialect)).strip())
    return parse_schema("\n".join(stmts))


# ---------------------------------------------------------------------------
# known intentional drift
# ---------------------------------------------------------------------------

# FKs that models.py (the source of truth) declares but V001 deliberately does
# *not* carry, keyed by ``(table, fk)`` using the parser's canonical FK form
# ``(local_cols, (referred_table, referred_cols))``.
#
# This is a narrow, explicitly enumerated excuse list - NOT a general "a FK may
# be missing" relaxation. Only the exact ``(table, fk)`` pairs named here are
# tolerated, on the models->V001 side only; every other missing/extra column,
# PK, UQ, FK, CHECK or index still fails the check.
#
#   group_indicators :: (('group_oid',), ('groups', ('oid',)))
#     GROUP_INDICATORS.GROUP_OID -> GROUPS(OID) cannot live in V001: a fresh
#     V001-only instance loads legacy ``zvms.groups`` ids via the ETL, so a
#     validated FK would fail with ORA-02298. It is supplied instead by
#     ``V009__statistics_rework.sql`` (see its ORDERING WARNING): the nightly
#     set-based rebuild rewrites GROUP_OID into the current GROUPS.OID, after
#     which the FK is satisfiable and V009 adds it. Retire this entry (folding
#     the FK back into V001) once the Mongo/ETL ingestion path is gone and
#     legacy group ids can no longer reach a V001-built instance.
MODELS_ONLY_FKS = {
    ("group_indicators", (("group_oid",), ("groups", ("oid",)))),
}


def _models_only_fks(table: str) -> set:
    """Return the FK definitions excused for ``table`` (see MODELS_ONLY_FKS)."""
    return {fk for tbl, fk in MODELS_ONLY_FKS if tbl == table}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _diff_table(name: str, expected: Dict, actual: Dict) -> List[str]:
    problems: List[str] = []

    exp_cols, act_cols = expected["columns"], actual["columns"]
    for col in sorted(set(exp_cols) - set(act_cols)):
        problems.append(f"    missing column: {col}")
    for col in sorted(set(act_cols) - set(exp_cols)):
        problems.append(f"    extra column: {col}")
    for col in sorted(set(exp_cols) & set(act_cols)):
        if exp_cols[col] != act_cols[col]:
            problems.append(
                f"    column {col} differs: models={exp_cols[col]} v001={act_cols[col]}"
            )

    for key in ("pk", "uq", "fk", "ck"):
        if key == "pk":
            exp_set = {expected["pk"]} if expected["pk"] is not None else set()
            act_set = {actual["pk"]} if actual["pk"] is not None else set()
        else:
            exp_set, act_set = expected[key], actual[key]
        if key == "fk":
            # Excuse only the explicitly enumerated remap-gated FKs that V001
            # deliberately omits (carried by later migrations); see
            # MODELS_ONLY_FKS. This does not relax any other FK anywhere.
            exp_set = exp_set - _models_only_fks(name)
        for item in sorted(exp_set - act_set, key=repr):
            problems.append(f"    missing {key.upper()}: {item}")
        for item in sorted(act_set - exp_set, key=repr):
            problems.append(f"    extra {key.upper()}: {item}")
    return problems


def main() -> int:
    expected = parse_models()      # SQLAlchemy Core is the source of truth
    actual = parse_v001()          # V001 is the artifact under test

    ok = True
    exp_tables = expected["tables"]
    act_tables = actual["tables"]

    for name in sorted(set(exp_tables) | set(act_tables)):
        if name not in act_tables:
            print(f"FAIL  {name}: missing from V001")
            ok = False
            continue
        if name not in exp_tables:
            print(f"FAIL  {name}: extra table in V001 not in models")
            ok = False
            continue
        problems = _diff_table(name, exp_tables[name], act_tables[name])
        if problems:
            ok = False
            print(f"FAIL  {name}")
            print("\n".join(problems))
        else:
            print(f"PASS  {name}")

    exp_idx, act_idx = expected["indexes"], actual["indexes"]
    for item in sorted(exp_idx - act_idx, key=repr):
        print(f"FAIL  index missing from V001: {item}")
        ok = False
    for item in sorted(act_idx - exp_idx, key=repr):
        print(f"FAIL  extra index in V001 not in models: {item}")
        ok = False
    if exp_idx == act_idx:
        print(f"PASS  indexes ({len(exp_idx)})")

    print()
    if ok:
        print(f"OK  {len(exp_tables)} tables, {len(exp_idx)} indexes - no drift")
        return 0
    print("DRIFT DETECTED - models.py and V001 are out of sync")
    return 1


if __name__ == "__main__":
    sys.exit(main())
