"""``misc/apply_ddl.py`` 的切分逻辑（离线，不建连接）。

迁移文件用 SQLcl 的 ``/`` 终止符：一个 PL/SQL 块必须以它收尾，普通 DDL 不能带
行尾分号（ORA-00911）。生产机没有 SQLcl，这个 runner 是唯一的应用路径，切错
一条就等于把 DDL 拆成半截——所以把它单独钉住。
"""

from __future__ import annotations

from misc.apply_ddl import _strip_plain_terminator, split_statements


def test_plsql_block_keeps_its_semicolon():
    sql = "BEGIN\n  NULL;\nEND;\n/\n"

    statements = split_statements(sql)

    assert statements == ["BEGIN\n  NULL;\nEND;"]


def test_plain_ddl_drops_the_trailing_semicolon():
    sql = "CREATE TABLE x (\n  id NUMBER\n);\n/\n"

    assert split_statements(sql) == ["CREATE TABLE x (\n  id NUMBER\n)"]


def test_comment_only_chunks_are_skipped():
    sql = "-- header\n-- more\n/\n\nALTER TABLE x ADD (y NUMBER);\n/\n"

    assert split_statements(sql) == ["ALTER TABLE x ADD (y NUMBER)"]


def test_tail_without_a_terminator_is_still_applied():
    assert split_statements("MERGE INTO x USING y ON (1=1);\n") == [
        "MERGE INTO x USING y ON (1=1)"
    ]


def test_plsql_prefix_is_detected_case_insensitively():
    assert _strip_plain_terminator("  begin\n null;\nend;").endswith("end;")
    assert _strip_plain_terminator("select 1 from dual;") == "select 1 from dual"
