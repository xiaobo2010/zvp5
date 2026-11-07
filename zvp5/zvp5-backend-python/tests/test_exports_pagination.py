"""``GET /api/exports``：分页/时间窗在 SQL 层收敛，导出循环复用活动集合。"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import oracle

from db import backends
from routers import exports_router as exports


def _sql(statement):
    return str(
        statement.compile(
            dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _Session:
    def __init__(self, total, rows):
        self._queue = [_Result(scalar=total), _Result(rows)]
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self._queue.pop(0)


def test_page_statement_is_bounded_in_sql_not_in_python():
    cutoff = datetime(2026, 9, 22, 12, 0, 0)
    sql = _sql(exports._task_page_stmt(cutoff, 3, 50))

    assert "coalesce(export_tasks.task_end, export_tasks.task_start)" in sql
    assert "OFFSET 100 ROWS" in sql
    assert "FETCH FIRST 50 ROWS ONLY" in sql


def test_page_size_is_capped():
    assert exports._MAX_EXPORT_PAGE_SIZE == 100
    assert exports._DEFAULT_EXPORT_WINDOW_DAYS == 30
    assert exports._MAX_EXPORT_WINDOW_DAYS > exports._DEFAULT_EXPORT_WINDOW_DAYS


@pytest.mark.asyncio
async def test_list_pages_through_the_database_and_keeps_data_a_list(monkeypatch):
    def unbounded(*args, **kwargs):
        raise AssertionError("list_all must not be used by the export list")

    monkeypatch.setattr(backends, "get_tasks_repo", unbounded)
    task_uuid = uuid.uuid4()
    started = datetime(2026, 10, 1, 8, 0, 0)
    session = _Session(
        total=7,
        rows=[
            SimpleNamespace(
                _mapping={
                    "oid": "oid-1",
                    "task_uuid": str(task_uuid),
                    "status": "completed",
                    "variant": "time",
                    "export_format": "excel",
                    "task_start": started,
                    "task_end": started,
                    "percentage": 100.0,
                }
            )
        ],
    )

    body = await exports.get_export_list(
        page=2, size=2, days=7, user={"id": "admin", "per": ["admin"]}, session=session
    )

    assert isinstance(body["data"], list)
    assert body["data"] == [
        {
            "_id": "oid-1",
            "id": task_uuid,
            "status": "completed",
            "variant": "time",
            "format": "excel",
            "task_start": started,
            "task_end": started,
            "percentage": 100.0,
        }
    ]
    assert (body["page"], body["size"], body["days"], body["total"]) == (2, 2, 7, 7)
    assert body["code"] == 200 and body["status"] == "ok"

    count_sql, page_sql = (_sql(statement) for statement in session.statements)
    assert "count(*)" in count_sql
    assert "OFFSET 2 ROWS" in page_sql
    assert "FETCH FIRST 2 ROWS ONLY" in page_sql


@pytest.mark.asyncio
async def test_list_requires_admin_before_touching_the_database():
    with pytest.raises(HTTPException) as failure:
        await exports.get_export_list(
            page=1, size=20, days=30, user={"per": ["student"]}, session=None
        )
    assert failure.value.status_code == 403


@pytest.mark.asyncio
async def test_time_export_computes_every_user_time_in_one_bulk_call(monkeypatch):
    seen = []

    async def list_all():
        return [
            {"_id": f"{index:024x}", "name": "Name", "id": "123", "class_id": "class"}
            for index in range(25)
        ]

    async def group_list():
        return [{"_id": "class", "name": "Group"}]

    async def bulk(ids, start=None, end=None, allow_cache=True):
        seen.append((list(ids), start, end, allow_cache))
        return {
            user_id: {"on-campus": 1, "off-campus": 0, "social-practice": 0}
            for user_id in ids
        }

    async def get_task(value):
        return {
            "variant": "time",
            "export_start": None,
            "export_end": None,
            "allow_cache": True,
            "include_description": False,
        }

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(
        backends,
        "get_tasks_repo",
        lambda: SimpleNamespace(
            get_by_uuid=get_task, update_fields=nothing, push_result=nothing
        ),
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda: SimpleNamespace(list_all=list_all))
    monkeypatch.setattr(
        backends, "get_groups_repo", lambda: SimpleNamespace(list_all=group_list)
    )
    monkeypatch.setattr(exports, "calculate_user_time_bulk", bulk)

    await exports.process_task(str(uuid.uuid4()))

    assert len(seen) == 1
    ids, start, end, allow_cache = seen[0]
    assert len(ids) == 25
    assert (start, end, allow_cache) == (None, None, True)
