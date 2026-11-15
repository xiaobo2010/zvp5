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


def test_backend_root_is_importable_when_run_as_a_script():
    """``python misc/apply_ddl.py`` 的 ``sys.path[0]`` 是 ``misc/``。

    不把后端根塞回去，脚本在服务器上会在 ``from db.oracle.session import ...``
    处直接 ModuleNotFoundError——本地 dry-run 不导入数据库层，所以只有实际执行
    才暴露。这里钉住「导入这个模块后 db 可见」。
    """
    import sys

    import misc.apply_ddl as module

    assert str(module._BACKEND_ROOT) in sys.path


def test_comment_header_does_not_hide_the_plsql_block():
    """迁移文件头有几十行注释：判断 PL/SQL 必须看第一条可执行行。

    回归：旧实现用 ``chunk.lstrip()`` 判 ``BEGIN``，被注释头挡住后把 ``END;``
    的分号剥掉，服务器上直接 ORA-06550（line 59, column 3）。
    """
    sql = "-- header\n-- more\nBEGIN\n  NULL;\nEND;\n/\n"

    statements = split_statements(sql)

    assert len(statements) == 1
    assert statements[0].endswith("END;")
