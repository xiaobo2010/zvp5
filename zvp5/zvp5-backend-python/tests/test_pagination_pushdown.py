"""分页下推：SQL 侧 ``OFFSET/FETCH`` + ``COUNT(*) OVER()``，不再全表加载。

被固化的五条：

1. ``users.search`` 的分页写在 SQL 里（``OFFSET n ROWS FETCH FIRST m ROWS
   ONLY``），排序键是 ``business_id, oid``（``oid`` 兜底保证分页不漏行/不重复）；
2. 一页数据只发**一条**语句，总数由窗口函数带回；只有越界空页才补一条 COUNT；
3. ``groups.list_classes`` 同一范式，且 ``size``/``page`` 被夹到合法区间；
4. ``GET /api/v1/users`` 把 ``page``/``size`` 原样交给 ``users.list_page``，
   页号为 0/-1 时按 ``Query(ge=1)`` 直接 422（合并前的「page=-1 全量」
  哨兵随未版本化实现一起删除）；
5. ``GET /api/v1/groups`` 只调一次 ``list_classes``（原来先 COUNT 再整表拉回）。

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
from db.repos.oracle.activities import _page_stmt as _activity_page_stmt
from db.repos.oracle.users import (
    OracleUsersRepo,
    _search_page_stmt,
    _user_page_stmt,
)
from db.repos.pagination import LIST_PAGE_SIZE_MAX
from routers import groups_router, users_router
from util.object_id import get_current_user

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


class _FakeUsersListRepo:
    def __init__(self, rows: list[dict], total: int) -> None:
        self.rows = rows
        self.total = total
        self.calls: list[tuple] = []

    async def list_page(self, query: str, role: str, page: int, size: int):
        self.calls.append((query, role, page, size))
        start = (page - 1) * size
        return [dict(row) for row in self.rows[start : start + size]], self.total


def _users_client() -> TestClient:
    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1/users")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    return TestClient(app)


def _patch_users_repo(monkeypatch, repo) -> None:
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)


def test_user_list_forwards_page_and_size_to_sql(monkeypatch) -> None:
    rows = [
        {"_id": f"{index:024x}", "name": f"学生{index}", "id": f"2026{index:04d}",
         "class_id": None}
        for index in range(30)
    ]
    repo = _FakeUsersListRepo(rows, 97)
    _patch_users_repo(monkeypatch, repo)

    response = _users_client().get(
        "/api/v1/users", params={"page": 3, "size": 5}
    )

    assert response.status_code == 200
    body = response.json()
    assert repo.calls == [("", "", 3, 5)]
    assert body["total"] == 97  # 总数来自 SQL，不是本页长度
    assert len(body["data"]) == 5
    assert body["data"][0]["_id"] == f"{10:024x}"


def test_user_list_rejects_non_positive_page(monkeypatch) -> None:
    repo = _FakeUsersListRepo([], 0)
    _patch_users_repo(monkeypatch, repo)

    response = _users_client().get("/api/v1/users", params={"page": -1})

    assert response.status_code == 422
    assert repo.calls == []


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
        "/api/groups", params={"page": 2, "size": 2}
    )

    assert response.status_code == 200
    body = response.json()
    # 合并顺序：班级页（3 条，仓储顺序）在前，权限组（1 条，按 name）在后。
    assert [row["name"] for row in body["data"]] == ["高二(1)班", "志工部"]
    assert body["total"] == 4


# ---------------------------------------------------------------------------
# 超大页的仓库层夹取（Query 校验不动，老客户端传 le=1000 不 422）
# ---------------------------------------------------------------------------


def test_activity_page_clamps_an_oversized_size(caplog) -> None:
    """活动列表 ``size=1000`` 在语句层就被夹到共享上限，并留一条 warning。"""
    sql = _sql(_activity_page_stmt("", None, None, 1, 1000, None, False))

    assert f"FETCH FIRST {LIST_PAGE_SIZE_MAX} ROWS ONLY" in sql
    assert any(
        "activities.list_page" in record.getMessage()
        for record in caplog.records
    )


def test_user_page_clamps_an_oversized_size(caplog) -> None:
    sql = _sql(_user_page_stmt("", "", 1, 1000))

    assert f"FETCH FIRST {LIST_PAGE_SIZE_MAX} ROWS ONLY" in sql
    assert any(
        "users.list_page" in record.getMessage()
        for record in caplog.records
    )


def test_a_size_within_the_ceiling_is_not_clamped(caplog) -> None:
    sql = _sql(_user_page_stmt("", "", 1, 50))

    assert "FETCH FIRST 50 ROWS ONLY" in sql
    assert caplog.records == []


# ---------------------------------------------------------------------------
# GET /api/users/{oid}/activities：Python 侧的过滤/排序/切片下推到 SQL
# ---------------------------------------------------------------------------


def _membership(member: str, mode: str = "on-campus") -> dict:
    return {
        "_id": f"{member}:{mode}",
        "activity": None,
        "member": member,
        "status": "effective",
        "mode": mode,
        "duration": 1.5,
    }


def _user_activity(oid: str, name: str) -> dict:
    return {
        "_id": oid,
        "name": name,
        "description": "",
        "type": "on-campus",
        "status": "effective",
        "date": "2026-09-01",
        "special": False,
    }


class _FakeUserActivitiesRepo:
    """按 SQL 的 OFFSET/FETCH 语义切片；``size=None`` 表示一次全量。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple] = []

    async def list_for_user_activities(self, user_oid, search, sort, asc, page, size):
        self.calls.append((user_oid, search, sort, asc, page, size))
        if size is None:
            window = self.rows
        else:
            start = (page - 1) * size
            window = self.rows[start : start + size]
        return [dict(row) for row in window], len(self.rows)


def _patch_user_activities(monkeypatch, rows: list[dict]) -> _FakeUserActivitiesRepo:
    repo = _FakeUserActivitiesRepo(rows)
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: repo)
    return repo


_USER_OID = "000000000000000000000001"
_ADMIN = {"id": "000000000000000000000002", "per": ["admin"]}


def _rows_for(oids: list[str]) -> list[dict]:
    return [
        {
            "member": _membership(_USER_OID),
            "activity": _user_activity(oid, f"活动{index}"),
        }
        for index, oid in enumerate(oids)
    ]


@pytest.mark.asyncio
async def test_user_activities_pages_in_sql(monkeypatch) -> None:
    repo = _patch_user_activities(monkeypatch, _rows_for([f"{i:024x}" for i in range(6)]))

    body = await users_router.get_user_activities_v2(
        _USER_OID, user=_ADMIN, page=2, perpage=2, search="", sort="_id", asc=False
    )

    # 分页参数原样下推（_id 倒序，与老路径的 Python 排序同一把尺子）。
    assert repo.calls == [(_USER_OID, "", "_id", False, 2, 2)]
    # 第二页就是 SQL 给的窗口，响应不再二次切片。
    assert [row["_id"] for row in body["activities"]] == [f"{2:024x}", f"{3:024x}"]
    assert body["total"] == 6
    assert body["page"] == 2
    assert body["perpage"] == 2


@pytest.mark.asyncio
async def test_user_activities_total_comes_from_the_repo(monkeypatch) -> None:
    """``total`` 是 SQL 窗口计数，不是本页长度。"""
    rows = _rows_for([f"{i:024x}" for i in range(6)])
    repo = _patch_user_activities(monkeypatch, rows)

    body = await users_router.get_user_activities_v2(
        _USER_OID, user=_ADMIN, page=1, perpage=10, search="", sort="_id", asc=False
    )

    assert repo.calls == [(_USER_OID, "", "_id", False, 1, 10)]
    assert len(body["activities"]) == 6
    assert body["total"] == 6


@pytest.mark.asyncio
async def test_user_activities_embed_mine_and_skip_missing_activities(
    monkeypatch,
) -> None:
    """每条成员行嵌一个 ``mine``；活动已删除（``activity is None``）的行不进响应。"""
    first = f"{1:024x}"
    second = f"{2:024x}"
    rows = [
        {"member": _membership(_USER_OID, "on-campus"), "activity": _user_activity(first, "A")},
        {"member": _membership(_USER_OID, "off-campus"), "activity": _user_activity(first, "A")},
        {"member": _membership(_USER_OID), "activity": None},
        {"member": _membership(_USER_OID), "activity": _user_activity(second, "B")},
    ]
    _patch_user_activities(monkeypatch, rows)

    body = await users_router.get_user_activities_v2(
        _USER_OID, user=_ADMIN, page=1, perpage=10, search="", sort="_id", asc=False
    )

    assert [row["_id"] for row in body["activities"]] == [first, first, second]
    assert [row["mine"]["mode"] for row in body["activities"]] == [
        "on-campus",
        "off-campus",
        "on-campus",
    ]
    # 成员行的内部关联键不外泄，``_id`` 统一成字符串。
    assert all("activity" not in row["mine"] for row in body["activities"])
    assert all(isinstance(row["mine"]["_id"], str) for row in body["activities"])


@pytest.mark.asyncio
async def test_user_activities_pushes_the_keyword_filter_into_sql(monkeypatch) -> None:
    """名字关键字是 SQL 谓词（同一个孪生体），不再在 Python 里 re.search。"""
    repo = _patch_user_activities(monkeypatch, [])

    await users_router.get_user_activities_v2(
        _USER_OID, user=_ADMIN, page=1, perpage=10, search="支教", sort="_id", asc=False
    )

    assert repo.calls == [(_USER_OID, "支教", "_id", False, 1, 10)]
