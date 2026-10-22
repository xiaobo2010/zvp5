"""分页下推：SQL 侧 ``OFFSET/FETCH`` + ``COUNT(*) OVER()``，不再全表加载。

被固化的五条：

1. ``users.search`` 的分页写在 SQL 里（``OFFSET n ROWS FETCH FIRST m ROWS
   ONLY``），排序键是 ``business_id, oid``（``oid`` 兜底保证分页不漏行/不重复）；
2. 一页数据只发**一条**语句，总数由窗口函数带回；只有越界空页才补一条 COUNT；
3. ``groups.list_classes`` 同一范式，且 ``size``/``page`` 被夹到合法区间；
4. ``GET /api/users`` 把 ``page``/``perpage`` 翻成 ``limit``/``offset``，
   ``page=-1`` 仍不分页但有 1000 的硬上限；
5. ``GET /api/groups`` 只调一次 ``list_classes``（原来先 COUNT 再整表拉回）。

离线机制：假 session 按语句自带的 limit/offset 对一个内存行表切片（模拟
Oracle 的 OFFSET/FETCH 语义），并回放窗口计数；不建连接、不连 ADB。

    cd <repo root> && .venv/bin/python -m pytest tests/test_pagination_pushdown.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import oracle

from db import backends
from db.repos.oracle.groups import (
    _CLASS_LIST_CAP,
    OracleGroupsRepo,
    _list_classes_stmt,
)
from db.repos.oracle.users import (
    OracleUsersRepo,
    _search_page_stmt,
)
from routers import groups_router, users_router
from util.object_id import get_current_user, optional_current_user

ADMIN = "000000000000000000000001"


class _Row:
    """``result.all()`` 里的行：这些用例只读 ``_mapping``。"""

    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Result:
    def __init__(self, rows: list[_Row], scalar=None) -> None:
        self._rows = rows
        self._scalar = scalar

    def all(self) -> list[_Row]:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if self._scalar is not None:
            return self._scalar
        return self._rows[0][0]


def _pagination(stmt) -> tuple[int | None, int]:
    """语句自带的 ``(limit, offset)``；SQLAlchemy 2.0 把它们挂在私有 clause 上。"""
    limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
    offset = getattr(getattr(stmt, "_offset_clause", None), "value", None) or 0
    return limit, offset


class _PagingSession:
    """内存行表 + 按语句的 limit/offset 切片，并注入 ``total_count``。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.statements: list = []

    async def execute(self, stmt) -> _Result:
        self.statements.append(stmt)
        columns = list(stmt.selected_columns.keys())
        if len(columns) == 1 and columns[0].startswith("count"):
            # 越界页的 COUNT 兜底：只回一个标量。
            return _Result([], scalar=len(self.rows))
        limit, offset = _pagination(stmt)
        window = self.rows[offset:] if limit is None else self.rows[offset : offset + limit]
        total = len(self.rows)
        return _Result([_Row({**row, "total_count": total}) for row in window])


def _user_rows(count: int) -> list[dict]:
    # 学号故意「倒序」构造，证明返回顺序来自 SQL 的 business_id 排序，
    # 而不是插入顺序。
    return [
        {
            "oid": f"{index:024x}",
            "business_id": f"{9000 - index:08d}",
            "name": f"学生{index}",
            "class_oid": "0000000000000000000000ff",
        }
        for index in range(count)
    ]


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------------------
# users repo
# ---------------------------------------------------------------------------


def test_search_pushes_page_and_window_count_into_sql() -> None:
    sql = _sql(_search_page_stmt("", True, 10, 20))

    assert "OFFSET 20 ROWS" in sql
    assert "FETCH FIRST 10 ROWS ONLY" in sql
    assert "count(*) OVER () AS total_count" in sql
    # 排序键必须是确定的：business_id（展示序）+ oid（唯一列）
    assert "ORDER BY users.business_id ASC, users.oid ASC" in sql


def test_search_without_limit_renders_no_fetch_clause() -> None:
    sql = _sql(_search_page_stmt("", True, None, 0))

    assert "FETCH FIRST" not in sql
    assert "OFFSET" not in sql


@pytest.mark.asyncio
async def test_search_returns_one_page_from_one_statement() -> None:
    session = _PagingSession(_user_rows(30))
    repo = OracleUsersRepo(session)

    rows, total = await repo.search("", True, limit=10, offset=20)

    assert len(rows) == 10
    assert total == 30
    assert len(session.statements) == 1  # 没有第二条 COUNT
    assert [row["id"] for row in rows] == [
        f"{9000 - index:08d}" for index in range(20, 30)
    ]


@pytest.mark.asyncio
async def test_search_offset_is_honoured_across_pages() -> None:
    session = _PagingSession(_user_rows(25))
    repo = OracleUsersRepo(session)

    first, _ = await repo.search("", True, limit=10, offset=0)
    second, _ = await repo.search("", True, limit=10, offset=10)

    assert [row["_id"] for row in first] == [f"{i:024x}" for i in range(10)]
    assert [row["_id"] for row in second] == [f"{i:024x}" for i in range(10, 20)]
    # 两页没有重叠、也没有漏行
    assert not ({row["_id"] for row in first} & {row["_id"] for row in second})


@pytest.mark.asyncio
async def test_search_out_of_range_page_falls_back_to_one_count() -> None:
    session = _PagingSession(_user_rows(5))
    repo = OracleUsersRepo(session)

    rows, total = await repo.search("", True, limit=10, offset=100)

    assert rows == []
    assert total == 5


@pytest.mark.asyncio
async def test_search_total_comes_from_the_window_not_from_len() -> None:
    session = _PagingSession(_user_rows(25))
    repo = OracleUsersRepo(session)

    rows, total = await repo.search("", True, limit=5, offset=0)

    assert len(rows) == 5
    assert total == 25  # 页大小 ≠ 总数


# ---------------------------------------------------------------------------
# groups repo
# ---------------------------------------------------------------------------


def _class_rows(count: int) -> list[dict]:
    return [
        {
            "oid": f"{index:024x}",
            "name": f"高一({index})班",
            "description": "",
        }
        for index in range(count)
    ]


def test_list_classes_pushes_page_and_window_count_into_sql() -> None:
    sql = _sql(_list_classes_stmt("", 2, 3))

    assert "OFFSET 3 ROWS" in sql
    assert "FETCH FIRST 3 ROWS ONLY" in sql
    assert "count(*) OVER () AS total_count" in sql


@pytest.mark.asyncio
async def test_list_classes_reads_one_statement_and_reports_total() -> None:
    session = _PagingSession(_class_rows(7))
    repo = OracleGroupsRepo(session)

    rows, total = await repo.list_classes("", 2, 5)

    assert [row["name"] for row in rows] == ["高一(5)班", "高一(6)班"]
    assert total == 7
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_list_classes_clamps_page_and_size() -> None:
    session = _PagingSession(_class_rows(3))
    repo = OracleGroupsRepo(session)

    await repo.list_classes("", 0, 99_999)

    limit, offset = _pagination(session.statements[0])
    assert (limit, offset) == (_CLASS_LIST_CAP, 0)


# ---------------------------------------------------------------------------
# GET /api/users
# ---------------------------------------------------------------------------


class _FakeUsersRepo:
    def __init__(self, rows: list[dict], total: int) -> None:
        self.rows = rows
        self.total = total
        self.calls: list[dict] = []

    async def search(self, query, logged_in, limit=None, offset=0):
        self.calls.append(
            {"query": query, "logged_in": logged_in, "limit": limit, "offset": offset}
        )
        window = self.rows if limit is None else self.rows[offset : offset + limit]
        return [dict(row) for row in window], self.total


def _users_client() -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/users")
    app.dependency_overrides[optional_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    return TestClient(app)


def _patch_users_repo(monkeypatch, repo) -> None:
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)


def test_read_users_translates_page_into_limit_and_offset(monkeypatch) -> None:
    rows = [
        {"_id": f"{index:024x}", "name": f"学生{index}", "id": f"2026{index:04d}",
         "class_id": None}
        for index in range(30)
    ]
    repo = _FakeUsersRepo(rows, 97)
    _patch_users_repo(monkeypatch, repo)

    response = _users_client().get(
        "/api/users", params={"page": 3, "perpage": 5}
    )

    assert response.status_code == 200
    body = response.json()
    assert repo.calls == [
        {"query": "", "logged_in": True, "limit": 5, "offset": 10}
    ]
    assert body["metadata"]["size"] == 97  # 总数来自 SQL，不是本页长度
    assert len(body["data"]) == 5
    assert body["data"][0]["_id"] == f"{10:024x}"


def test_read_users_unpaged_branch_is_capped(monkeypatch) -> None:
    repo = _FakeUsersRepo([], 4200)
    _patch_users_repo(monkeypatch, repo)

    response = _users_client().get("/api/users", params={"page": -1})

    assert response.status_code == 200
    assert repo.calls == [
        {
            "query": "",
            "logged_in": True,
            "limit": users_router._USER_LIST_CAP,
            "offset": 0,
        }
    ]
    assert users_router._USER_LIST_CAP == 1000


# ---------------------------------------------------------------------------
# GET /api/groups
# ---------------------------------------------------------------------------


class _FakeGroupsRepo:
    """记录调用次数：班级列表只该被问一次。"""

    def __init__(self) -> None:
        self.class_calls: list[dict] = []
        self.rows = [
            {"_id": "000000000000000000000011", "name": "高一(1)班", "description": ""},
            {"_id": "000000000000000000000012", "name": "高一(2)班", "description": ""},
            {"_id": "000000000000000000000013", "name": "高二(1)班", "description": ""},
        ]
        self.permissions = [
            {
                "_id": "0000000000000000000000ff",
                "name": "志工部",
                "type": "permission",
                "permissions": ["activity:read"],
                "description": "",
            }
        ]

    async def list_classes(self, query: str = "", page: int = 1, size: int = 1000):
        self.class_calls.append({"query": query, "page": page, "size": size})
        return [dict(row) for row in self.rows], len(self.rows)

    async def list_permission_groups(self, query: str = "", permissions=None):
        return [dict(row) for row in self.permissions]


def _groups_client() -> TestClient:
    app = FastAPI()
    app.include_router(groups_router.router, prefix="/api/groups")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    return TestClient(app)


def test_get_groups_asks_the_class_list_once(monkeypatch) -> None:
    repo = _FakeGroupsRepo()
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)

    response = _groups_client().get("/api/groups", params={"type": "class"})

    assert response.status_code == 200
    assert repo.class_calls == [
        {"query": "", "page": 1, "size": groups_router._CLASS_LIST_CAP}
    ]


def test_get_groups_still_slices_the_merged_list(monkeypatch) -> None:
    repo = _FakeGroupsRepo()
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)

    response = _groups_client().get(
        "/api/groups", params={"type": "all", "page": 2, "perpage": 2}
    )

    assert response.status_code == 200
    body = response.json()
    # 合并后按 name 排序：志工部 < 高一(1)班 < 高一(2)班 < 高二(1)班
    assert [row["name"] for row in body["data"]] == ["高一(2)班", "高二(1)班"]
    assert body["metadata"]["size"] == 4
