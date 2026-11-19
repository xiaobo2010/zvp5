"""通知版本历史的写入路径契约（离线，脚本化会话，不连库）。

钉住四件容易写错且错了会静默破坏审计的事：

* 每次成功写入在同一事务里落一条快照，且 ``op`` 与内容一致；
* 编辑先 ``SELECT ... FOR UPDATE`` 再 UPDATE，并发编辑不会抢同一个版本号；
* 删除快照先于硬删，且不占用通知行已有的版本号；
* 回滚到已删除通知时用**原 oid** 重新插入，版本号取历史最大值 + 1。

Nothing executes against a database: a scripted session records the compiled
statements and replays canned results.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_notification_versions.py -q
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.dialects import oracle

from db.repos.oracle import notifications as repo_mod

OID = "0d00000000000000000000aa"
AUTHOR_OID = "000000000000000000000001"
ACTOR_OID = "000000000000000000000002"
NOW = datetime(2026, 10, 21, 9, 30, 0)

DIALECT = oracle.dialect()

TARGET = {
    "oid": "0e00000000000000000000b1",
    "notification_oid": OID,
    "version": 1,
    "title": "原标题",
    "content": "原正文",
    "active": 1,
    "op": "create",
    "source_version": None,
    "content_hash": "b" * 64,
    "author_oid": AUTHOR_OID,
    "author_name": "张老师",
    "created_at": NOW,
}


def _sql(stmt) -> str:
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, row=None, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row

    def all(self):
        return [] if self._row is None else [self._row]


class _ScriptedSession:
    """Replays canned results and records every compiled statement in order."""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.calls: list[str] = []
        self.commits = 0

    async def execute(self, stmt):
        self.calls.append(_sql(stmt))
        if not self._results:
            return _Result(None, rowcount=1)
        return self._results.pop(0)

    async def commit(self):
        self.commits += 1


def _index_of(calls: list[str], needle: str) -> int:
    for position, call in enumerate(calls):
        if needle in call:
            return position
    raise AssertionError(f"{needle!r} not found in:\n" + "\n".join(calls))


def test_update_locks_before_writing_and_snapshots_new_revision() -> None:
    updated_row = {
        "oid": OID,
        "title": "新标题",
        "content": "原正文",
        "active": 1,
        "author_oid": AUTHOR_OID,
        "author_name": "张老师",
        "version": 2,
        "created_at": NOW,
        "updated_at": NOW,
    }
    session = _ScriptedSession(
        [
            _Result(_Row(dict(updated_row, version=1))),  # FOR UPDATE
            _Result(None, rowcount=1),                     # UPDATE
            _Result(_Row(updated_row)),                    # re-read
            _Result(None, rowcount=1),                     # snapshot insert
            _Result(None, rowcount=1),                     # state bump
            _Result(_Row({"version": 7, "updated_at": NOW})),
        ]
    )
    repo = repo_mod.OracleNotificationsRepo(session=session)

    entry, version = asyncio.run(
        repo.update(
            OID,
            title="新标题",
            author_oid=ACTOR_OID,
            author_name="李老师",
        )
    )

    assert entry is not None and entry["version"] == 2
    assert version == 7
    assert session.commits == 0  # injected session is caller-owned

    lock_at = _index_of(session.calls, "FOR UPDATE")
    update_at = _index_of(session.calls, "UPDATE notifications SET")
    snapshot_at = _index_of(session.calls, "INSERT INTO notification_versions")
    assert lock_at < update_at < snapshot_at

    snapshot_sql = session.calls[snapshot_at]
    assert "'update'" in snapshot_sql
    assert f"'{OID}'" in snapshot_sql
    assert ", 2, '新标题'" in snapshot_sql
    assert "'李老师'" in snapshot_sql


def test_delete_snapshots_before_removing_with_a_free_revision() -> None:
    existing_row = {
        "oid": OID,
        "title": "标题",
        "content": "正文",
        "active": 0,
        "author_oid": AUTHOR_OID,
        "author_name": "张老师",
        "version": 2,
        "created_at": NOW,
        "updated_at": NOW,
    }
    session = _ScriptedSession(
        [
            _Result(_Row(existing_row)),  # FOR UPDATE
            _Result((2,)),                # max snapshot version
            _Result(None, rowcount=1),    # snapshot insert
            _Result(None, rowcount=1),    # DELETE
            _Result(None, rowcount=1),    # DELETE 该通知的已读行（V053）
            _Result(None, rowcount=1),    # state bump
            _Result(_Row({"version": 9, "updated_at": NOW})),
        ]
    )
    repo = repo_mod.OracleNotificationsRepo(session=session)

    deleted, version = asyncio.run(repo.delete(OID, author_name="李老师"))

    assert deleted is not None and deleted["active"] is False
    assert version == 9

    snapshot_at = _index_of(session.calls, "INSERT INTO notification_versions")
    delete_at = _index_of(session.calls, "DELETE FROM notifications")
    assert snapshot_at < delete_at

    snapshot_sql = session.calls[snapshot_at]
    assert "'delete'" in snapshot_sql
    assert ", 3, '标题', '正文', 0, 'delete'" in snapshot_sql


def test_rollback_of_deleted_notice_reuses_the_original_oid() -> None:
    session = _ScriptedSession(
        [
            _Result(None),               # FOR UPDATE -> gone
            _Result(_Row(TARGET)),       # target snapshot
            _Result((4,)),               # max snapshot version
            _Result(None, rowcount=1),   # re-insert
            _Result(None, rowcount=1),   # rollback snapshot
            _Result(None, rowcount=1),   # state bump
            _Result(_Row({"version": 11, "updated_at": NOW})),
        ]
    )
    repo = repo_mod.OracleNotificationsRepo(session=session)

    entry, version, snapshot = asyncio.run(
        repo.rollback(
            OID,
            target_version=1,
            author_oid=ACTOR_OID,
            author_name="李老师",
        )
    )

    assert entry is not None
    assert entry["_id"] == OID
    assert entry["title"] == "原标题"
    assert entry["version"] == 5
    assert version == 11
    assert snapshot is not None
    assert snapshot["op"] == "rollback"
    assert snapshot["source_version"] == 1
    assert snapshot["version"] == 5
    assert snapshot["content_hash"] == repo_mod.notification_content_hash(
        "原标题", "原正文", True
    )

    insert_at = _index_of(session.calls, "INSERT INTO notifications ")
    assert f"VALUES ('{OID}', '原标题', '原正文', 1," in session.calls[insert_at]
    assert ", 5, TO_TIMESTAMP(" in session.calls[insert_at]

    snapshot_at = _index_of(session.calls, "INSERT INTO notification_versions")
    assert ", 5, '原标题', '原正文', 1, 'rollback', 1," in session.calls[snapshot_at]
    assert "'李老师'" in session.calls[snapshot_at]


def test_rollback_of_existing_notice_is_an_edit() -> None:
    current_row = {
        "oid": OID,
        "title": "当前标题",
        "content": "当前正文",
        "active": 1,
        "author_oid": AUTHOR_OID,
        "author_name": "张老师",
        "version": 2,
        "created_at": NOW,
        "updated_at": NOW,
    }
    restored_row = dict(current_row, title="原标题", content="原正文", version=3)
    session = _ScriptedSession(
        [
            _Result(_Row(current_row)),   # FOR UPDATE
            _Result(_Row(TARGET)),        # target snapshot
            _Result(None, rowcount=1),    # UPDATE
            _Result(_Row(restored_row)),  # re-read
            _Result(None, rowcount=1),    # rollback snapshot
            _Result(None, rowcount=1),    # state bump
            _Result(_Row({"version": 12, "updated_at": NOW})),
        ]
    )
    repo = repo_mod.OracleNotificationsRepo(session=session)

    entry, version, snapshot = asyncio.run(
        repo.rollback(
            OID,
            target_version=1,
            author_oid=ACTOR_OID,
            author_name="李老师",
        )
    )

    assert entry is not None and entry["version"] == 3
    assert version == 12
    assert snapshot is not None and snapshot["source_version"] == 1
    assert not any(call.startswith("INSERT INTO notifications ") for call in session.calls)
    assert _index_of(session.calls, "FOR UPDATE") < _index_of(
        session.calls, "UPDATE notifications SET"
    )


def test_rollback_unknown_version_touches_nothing() -> None:
    session = _ScriptedSession(
        [
            _Result(_Row(TARGET)),  # FOR UPDATE -> row exists, but...
            _Result(None),          # ...target snapshot missing
            _Result(_Row({"version": 6, "updated_at": NOW})),
        ]
    )
    repo = repo_mod.OracleNotificationsRepo(session=session)

    entry, version, snapshot = asyncio.run(
        repo.rollback(
            OID,
            target_version=99,
            author_oid=ACTOR_OID,
            author_name="李老师",
        )
    )

    assert entry is None and snapshot is None
    assert version == 6
    assert not any(
        "INSERT INTO notification_versions" in call for call in session.calls
    )
    assert not any("UPDATE notifications SET" in call for call in session.calls)


# ---------------------------------------------------------------------------
# 迁移镜像（ddl_check 只比对 V001，这里额外钉住 V017 与 V001/models 一致）
# ---------------------------------------------------------------------------


def test_v017_migration_mirrors_models_and_v001() -> None:
    from db import ddl_check

    v017_path = ddl_check.DDL_PATH.parent / "V017__add_notification_versions.sql"
    v017 = ddl_check.parse_schema(
        ddl_check.extract_execute_immediate(v017_path.read_text(encoding="utf-8"))
    )
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["notification_versions"]
    # V047 起 created_at 带 DEFAULT SYSTIMESTAMP；V017 是历史事实，不追新目标态。
    legacy = {
        **expected,
        "columns": {**expected["columns"], "created_at": ("timestamp", True, None)},
    }
    assert v017["tables"]["notification_versions"] == legacy
    assert v001["tables"]["notification_versions"] == expected

    index = ("notification_versions", False, ("notification_oid", "version"))
    assert index in v017["indexes"]
    assert index in v001["indexes"]


def test_v017_is_idempotent_on_existing_objects() -> None:
    from db import ddl_check

    text = (
        ddl_check.DDL_PATH.parent / "V017__add_notification_versions.sql"
    ).read_text(encoding="utf-8")

    # 已存在表/索引/约束 -> ORA-00955 / 01408；其余错误必须继续抛。
    assert "-955" in text
    assert "-1408" in text
    assert "EXECUTE IMMEDIATE" in text
