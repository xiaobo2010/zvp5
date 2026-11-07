"""SQL 批量化与投影收敛的离线契约（不连库）。

钉住三条性能红线——它们错了不会报错，只会在远程 ADB 上把延迟乘以行数：

* 超过 1000 个 oid 的 ``IN`` 必须分块（ORA-01795），且分块后仍保持原排序/分页
  语义；
* 状态轮询的 SELECT 不得带 ``result`` CLOB；
* 批量写（导入成员、班级重算）必须是一条语句一批参数，而不是逐行往返。

所有语句都用 Oracle 方言编译，fake session 只数 execute 次数和参数。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import oracle

from db import backends
from db.repos.oracle import members as members_repo
from db.repos.oracle import tasks as tasks_repo
from db.repos.oracle import times as times_repo
from routers import exports_router as exports

DIALECT = oracle.dialect()


def _sql(statement) -> str:
    return " ".join(
        str(
            statement.compile(
                dialect=DIALECT, compile_kwargs={"literal_binds": True}
            )
        ).split()
    )


def _oid(index: int) -> str:
    return f"{index:024x}"


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Records ``execute`` calls; ``factory`` decides the rows per call."""

    def __init__(self, factory=None):
        self.calls: list[tuple[object, object]] = []
        self._factory = factory or (lambda statement, params: [])

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return _Result(self._factory(statement, params))


def _row(**values):
    return SimpleNamespace(_mapping=values)


# ---------------------------------------------------------------------------
# A. IN 列表分块
# ---------------------------------------------------------------------------


def _member_row(oid: str):
    return _row(
        oid=oid,
        activity_oid=_oid(10),
        member_oid=_oid(1),
        member_status="effective",
        member_mode="on-campus",
        duration_hours=1.0,
    )


@pytest.mark.asyncio
async def test_list_by_members_splits_more_than_1000_oids():
    session = _Session(lambda statement, params: [_member_row(_oid(11))])
    repo = members_repo.OracleMembersRepo(session=session)

    rows = await repo.list_by_members([_oid(index) for index in range(1001)])

    assert len(session.calls) == 2
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_list_by_members_keeps_a_single_statement_at_the_limit():
    session = _Session(lambda statement, params: [])
    repo = members_repo.OracleMembersRepo(session=session)

    await repo.list_by_members([_oid(index) for index in range(1000)])

    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_list_by_users_splits_more_than_1000_oids():
    session = _Session(lambda statement, params: [])
    repo = times_repo.OracleTimeRepo(session=session)

    await repo.list_by_users([_oid(index) for index in range(1001)])

    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_chunked_list_by_users_restores_global_order(monkeypatch):
    """分块后 ORDER BY 只作用于单块，外层必须按同一键补一次全局排序。"""
    monkeypatch.setattr(times_repo, "_OID_CHUNK", 2)

    def make_row(user_oid, value):
        return _row(
            oid=user_oid,
            user_oid=user_oid,
            on_campus_raw=value,
            off_campus_raw=0.0,
            social_practice=0.0,
            updated_at=None,
        )

    # 每块内部乱序，块与块之间也乱序；正确结果应按 on_campus_raw 升序。
    batches = {
        frozenset({_oid(1), _oid(2)}): [make_row(_oid(2), 9.0), make_row(_oid(1), 1.0)],
        frozenset({_oid(3), _oid(4)}): [make_row(_oid(4), 4.0), make_row(_oid(3), 2.0)],
    }

    def factory(statement, params):
        sql = _sql(statement)
        for keys, rows in batches.items():
            if all(f"'{key}'" in sql for key in keys):
                return rows
        return []

    session = _Session(factory)
    repo = times_repo.OracleTimeRepo(session=session)

    rows = await repo.list_by_users([_oid(1), _oid(2), _oid(3), _oid(4)], "on_campus_raw")

    assert [row["on_campus_raw"] for row in rows] == [1.0, 2.0, 4.0, 9.0]
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_chunked_list_by_users_applies_page_after_the_global_sort(monkeypatch):
    monkeypatch.setattr(times_repo, "_OID_CHUNK", 2)

    def factory(statement, params):
        sql = _sql(statement)
        if f"'{_oid(1)}'" in sql:
            return [
                _row(oid=_oid(1), user_oid=_oid(1), on_campus_raw=1.0, off_campus_raw=0.0, social_practice=0.0, updated_at=None),
                _row(oid=_oid(2), user_oid=_oid(2), on_campus_raw=4.0, off_campus_raw=0.0, social_practice=0.0, updated_at=None),
            ]
        if f"'{_oid(3)}'" in sql:
            return [
                _row(oid=_oid(3), user_oid=_oid(3), on_campus_raw=2.0, off_campus_raw=0.0, social_practice=0.0, updated_at=None),
                _row(oid=_oid(4), user_oid=_oid(4), on_campus_raw=9.0, off_campus_raw=0.0, social_practice=0.0, updated_at=None),
            ]
        return []

    repo = times_repo.OracleTimeRepo(session=_Session(factory))

    rows = await repo.list_by_users(
        [_oid(1), _oid(2), _oid(3), _oid(4)],
        "on_campus_raw",
        page=2,
        size=2,
    )

    assert [row["on_campus_raw"] for row in rows] == [4.0, 9.0]


# ---------------------------------------------------------------------------
# B. 状态轮询投影
# ---------------------------------------------------------------------------


def test_status_projection_never_selects_the_result_clob():
    sql = _sql(tasks_repo._get_status_by_uuid_stmt(str(uuid.uuid4())))

    assert "FROM export_tasks" in sql
    assert "export_tasks.result" not in sql
    assert "export_tasks.status" in sql
    assert "export_tasks.percentage" in sql


def test_full_task_projection_still_has_the_result_column():
    sql = _sql(tasks_repo._get_by_uuid_stmt(str(uuid.uuid4())))

    assert "export_tasks.result" in sql


@pytest.mark.asyncio
async def test_get_status_by_uuid_returns_no_result_key():
    task_uuid = str(uuid.uuid4())
    session = _Session(
        lambda statement, params: [
            _row(
                oid=_oid(1),
                task_uuid=task_uuid,
                status="completed",
                percentage=100.0,
                variant="time",
                export_format="excel",
                allow_cache=1,
                include_description=0,
                errmsg=None,
                export_start=None,
                export_end=None,
                task_start=None,
                task_end=None,
            )
        ]
    )
    repo = tasks_repo.OracleTasksRepo(session=session)

    task = await repo.get_status_by_uuid(task_uuid)

    assert task is not None
    assert "result" not in task
    assert task["status"] == "completed"
    assert task["allow_cache"] is True


@pytest.mark.asyncio
async def test_export_poll_route_uses_the_narrow_lookup(monkeypatch):
    task_uuid = str(uuid.uuid4())
    calls: list[str] = []

    async def get_status_by_uuid(value):
        calls.append(str(value))
        return {
            "_id": _oid(1),
            "id": uuid.UUID(task_uuid),
            "status": "completed",
            "percentage": 100.0,
        }

    async def forbidden(*args, **kwargs):
        raise AssertionError("状态轮询不得读取 result")

    monkeypatch.setattr(
        backends,
        "get_tasks_repo",
        lambda: SimpleNamespace(
            get_status_by_uuid=get_status_by_uuid, get_by_uuid=forbidden
        ),
    )

    body = await exports.get_export(task_uuid, user={"id": "admin", "per": ["admin"]})

    assert calls == [task_uuid]
    assert body["data"]["_id"] == _oid(1)
    assert "result" not in body["data"]


# ---------------------------------------------------------------------------
# D. 导入成员批量写入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_many_writes_one_execute_for_a_small_import():
    session = _Session()
    repo = members_repo.OracleMembersRepo(session=session)

    await repo.add_many(
        _oid(10),
        [(_oid(1), "on-campus", 1.5), (_oid(2), "off-campus", 2.0)],
        "effective",
    )

    assert len(session.calls) == 1
    statement, params = session.calls[0]
    assert statement.is_insert
    assert [row["member_oid"] for row in params] == [_oid(1), _oid(2)]
    assert all(row["activity_oid"] == _oid(10) for row in params)
    assert all(row["member_status"] == "effective" for row in params)


@pytest.mark.asyncio
async def test_add_many_chunks_1001_rows_into_two_executes():
    session = _Session()
    repo = members_repo.OracleMembersRepo(session=session)

    await repo.add_many(
        _oid(10),
        [(_oid(index), "on-campus", 1.0) for index in range(1001)],
        "effective",
    )

    assert [len(params) for _, params in session.calls] == [1000, 1]


def test_batch_insert_targets_the_membership_columns():
    sql = _sql(
        members_repo._insert_member_stmt(
            _oid(9), _oid(10), _oid(1), "on-campus", 1.0, "effective"
        )
    )

    for column in (
        "activity_members (oid, activity_oid, member_oid",
        "member_status",
        "member_mode",
        "duration_hours",
    ):
        assert column in sql


# ---------------------------------------------------------------------------
# E. 班级重算批量 upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_time_recompute_upserts_the_page_in_one_call(monkeypatch):
    from routers import groups_router

    students = [_oid(index) for index in range(1, 4)]
    users = [
        {"_id": student, "id": f"1{index}", "name": f"n{index}", "class_id": _oid(50)}
        for index, student in enumerate(students, start=1)
    ]
    batches: list[list[tuple]] = []

    async def list_by_class(class_oid):
        return users

    async def get_group(group_oid):
        return {"_id": group_oid, "name": "G"}

    async def calculate(ids, start, end, allow_cache=True):
        return {
            student: {"on-campus": 1.0, "off-campus": 2.0, "social-practice": 3.0}
            for student in ids
        }

    async def upsert_many_for_users(entries):
        batches.append(list(entries))

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(list_by_class=list_by_class),
    )
    monkeypatch.setattr(
        backends,
        "get_groups_repo",
        lambda session=None: SimpleNamespace(get=get_group),
    )
    monkeypatch.setattr(
        backends,
        "get_time_repo",
        lambda session=None: SimpleNamespace(
            upsert_many_for_users=upsert_many_for_users
        ),
    )
    monkeypatch.setattr(groups_router, "calculate_user_time_bulk", calculate)

    await groups_router.get_user_times_in_class(
        _oid(50),
        page=1,
        perpage=10,
        exceeding=True,
        shortage=False,
        start=None,
        end=None,
        search="",
        allow_cache=False,
        user={"id": _oid(1)},
    )

    assert len(batches) == 1
    assert [entry[0] for entry in batches[0]] == students
    assert batches[0][0][1:] == (1.0, 2.0, 3.0)


@pytest.mark.asyncio
async def test_upsert_many_for_users_uses_one_delete_and_one_execute():
    session = _Session()
    repo = times_repo.OracleTimeRepo(session=session)

    await repo.upsert_many_for_users(
        [(_oid(1), 1.0, 2.0, 3.0), (_oid(2), 4.0, 5.0, 6.0)]
    )

    assert len(session.calls) == 2
    delete_stmt, delete_params = session.calls[0]
    insert_stmt, insert_params = session.calls[1]
    assert delete_stmt.is_delete
    assert delete_params is None
    assert "user_time.user_oid IN (" in _sql(delete_stmt)
    assert insert_stmt.is_insert
    assert [row["user_oid"] for row in insert_params] == [_oid(1), _oid(2)]
    assert [row["on_campus_raw"] for row in insert_params] == [1.0, 4.0]


@pytest.mark.asyncio
async def test_upsert_many_for_users_keeps_the_last_value_per_user():
    session = _Session()
    repo = times_repo.OracleTimeRepo(session=session)

    await repo.upsert_many_for_users(
        [(_oid(1), 1.0, 0.0, 0.0), (_oid(1), 9.0, 0.0, 0.0)]
    )

    _, insert_params = session.calls[1]
    assert [row["on_campus_raw"] for row in insert_params] == [9.0]
