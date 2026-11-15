"""群组管理域的删除/校验/分页/搜索（离线单测）。

覆盖四组回归：

1. ``delete_cascade``：锁父行先于子表写、单事务单次 commit、不存在的班不写库、
   指标删除失败时既不影响成员绑定也不提交；
2. 群组列宽校验：``name`` 128 / ``description`` 1000 在 v1、v3 全部入口都是 400，
   NUL 字符同样 400（不静默截断）；
3. v3 删班的凭据闸门：长会话 token 进不去（401），临时 token 才落到 204；
4. 分页/搜索下推：v1 ``/user`` 把 page/size 传下去、v2 ``/time`` 认 ``search`` 别名。
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import oracle

from db import backends
from db.repos.oracle import groups as groups_mod
from routers import groups_router, groups_v2_router, groups_v3_router
from typings.log import inject_log
from util.cert import jwt_encode
from util.object_id import compulsory_temporary_token, get_current_user

GROUP = "000000000000000000000010"
ADMIN = {"id": "000000000000000000000001", "per": ["admin"], "perm": []}


# ---------------------------------------------------------------------------
# 仓储级：delete_cascade 的单事务级联
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._rows[0] if self._rows else 0


class _Session:
    """记录每条语句的 SQL 文本与 commit 次数；``fail_on`` 命中即抛错。"""

    def __init__(self, rows, fail_on=None):
        self._rows = rows
        self.fail_on = fail_on
        self.sql: list[str] = []
        self.commits = 0

    async def execute(self, stmt):
        text = str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        self.sql.append(text)
        if self.fail_on is not None and self.fail_on in text:
            raise RuntimeError("boom")
        if getattr(stmt, "is_select", False):
            return _Result(self._rows)
        return _Result([])

    async def commit(self):
        self.commits += 1


def _owned_session(monkeypatch, session):
    """让 repo 自建 session 的那条分支拿到 ``session``（owns=True → 会 commit）。"""

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(groups_mod, "session_scope", lambda: _scope())


@pytest.mark.asyncio
async def test_delete_cascade_locks_the_group_before_child_writes(monkeypatch):
    session = _Session([_Row({"oid": GROUP, "name": "甲班", "description": None})])
    _owned_session(monkeypatch, session)
    repo = groups_mod.OracleGroupsRepo()

    deleted = await repo.delete_cascade(GROUP)

    assert deleted == {"_id": GROUP, "name": "甲班", "description": ""}
    # 锁语句必须先于任何子表写：并发删 + 加人才不会反向抢锁（ORA-00060）。
    assert "FOR UPDATE" in session.sql[0]
    assert "group_indicators" in session.sql[1]
    assert session.sql[2].startswith("UPDATE users")
    assert session.sql[3].startswith("DELETE FROM groups")
    # 整段只提交一次。
    assert session.commits == 1


@pytest.mark.asyncio
async def test_delete_cascade_missing_group_writes_nothing(monkeypatch):
    session = _Session([])
    _owned_session(monkeypatch, session)

    assert await groups_mod.OracleGroupsRepo().delete_cascade(GROUP) is None
    assert len(session.sql) == 1
    assert session.commits == 0


@pytest.mark.asyncio
async def test_delete_cascade_failure_keeps_members_bound(monkeypatch):
    session = _Session(
        [_Row({"oid": GROUP, "name": "甲班", "description": ""})],
        fail_on="group_indicators",
    )
    _owned_session(monkeypatch, session)

    with pytest.raises(RuntimeError):
        await groups_mod.OracleGroupsRepo().delete_cascade(GROUP)

    # 指标行删不掉时：成员 UPDATE 根本没执行、也没有 commit（整段一起回滚）。
    assert not any(sql.startswith("UPDATE users") for sql in session.sql)
    assert session.commits == 0


# ---------------------------------------------------------------------------
# 路由级桩
# ---------------------------------------------------------------------------


class _FakeLog:
    def with_text(self, *_args, **_kwargs):
        return None

    async def insert_log(self):
        return None


class _GroupsRepo:
    def __init__(self, cascade_result=None):
        self.cascade_result = cascade_result
        self.cascade_calls: list[str] = []
        self.members_calls: list[dict] = []
        self.member_rows: list[dict] = []
        self.member_total = 0

    async def delete_cascade(self, oid):
        self.cascade_calls.append(oid)
        return self.cascade_result

    async def get(self, oid):
        return {"_id": oid, "name": "甲班", "description": ""}

    async def create(self, name):
        return GROUP

    async def rename(self, oid, name):
        return None

    async def set_description(self, oid, value):
        return None

    async def members_page(self, group_oid, query, page, size):
        self.members_calls.append(
            {"group_oid": group_oid, "query": query, "page": page, "size": size}
        )
        offset = (page - 1) * size
        return self.member_rows[offset : offset + size], self.member_total


class _TimeRepo:
    def __init__(self, rows, total=None):
        self.rows = rows
        self.total = total if total is not None else len(rows)
        self.calls: list[dict] = []

    async def list_by_class(
        self, class_oid, query="", sort_field=None, descending=False, page=None, size=None
    ):
        self.calls.append(
            {
                "class_oid": class_oid,
                "query": query,
                "sort_field": sort_field,
                "descending": descending,
                "page": page,
                "size": size,
            }
        )
        rows = [row for row in self.rows if not query or query in row["name"]]
        return rows, self.total


def _client(*routers, prefix_map=None):
    app = FastAPI()
    for router, prefix in routers:
        app.include_router(router, prefix=prefix)
    app.dependency_overrides[inject_log] = lambda: _FakeLog()
    return app


def _install(monkeypatch, groups_repo=None, time_repo=None):
    if groups_repo is not None:
        monkeypatch.setattr(
            backends, "get_groups_repo", lambda *a, **k: groups_repo
        )
    if time_repo is not None:
        monkeypatch.setattr(backends, "get_time_repo", lambda *a, **k: time_repo)
    # 日志文案里的显示名走 users 仓储；离线用例统一打桩，避免连库。
    async def _display_name(oid):
        return oid

    monkeypatch.setattr(groups_router, "get_user_display_name", _display_name)
    monkeypatch.setattr(groups_v3_router, "get_user_display_name", _display_name)


# ---------------------------------------------------------------------------
# 删除：v1 / v3 都走 delete_cascade，404 由锁内判定给出
# ---------------------------------------------------------------------------


def test_v1_delete_uses_the_cascade(monkeypatch):
    repo = _GroupsRepo(cascade_result={"_id": GROUP, "name": "甲班", "description": ""})
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_v3_router.router, "/api/v1/groups"))
    app.include_router(groups_router.router, prefix="/api/groups")
    app.dependency_overrides[compulsory_temporary_token] = lambda: ADMIN

    response = TestClient(app).delete(f"/api/groups/{GROUP}")

    assert response.status_code == 200
    assert repo.cascade_calls == [GROUP]


def test_v3_delete_missing_group_is_404(monkeypatch):
    repo = _GroupsRepo(cascade_result=None)
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_v3_router.router, "/api/v1/groups"))
    app.dependency_overrides[compulsory_temporary_token] = lambda: ADMIN

    assert TestClient(app).delete(f"/api/v1/groups/{GROUP}").status_code == 404


def test_v3_delete_is_gated_by_a_temporary_credential(monkeypatch):
    repo = _GroupsRepo(cascade_result={"_id": GROUP, "name": "甲班", "description": ""})
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_v3_router.router, "/api/v1/groups"))

    # 长会话 token（scope=access_token）不是删班凭据。
    long_token = jwt_encode(ADMIN["id"], [], ["admin"], type="long")
    denied = TestClient(app).delete(
        f"/api/v1/groups/{GROUP}",
        headers={"Authorization": f"Bearer {long_token}"},
    )
    assert denied.status_code == 401
    assert repo.cascade_calls == []

    # 临时 token 闸门本身仍然放行（依赖替身代表校验通过）。
    app.dependency_overrides[compulsory_temporary_token] = lambda: ADMIN
    allowed = TestClient(app).delete(f"/api/v1/groups/{GROUP}")
    assert allowed.status_code == 204
    assert repo.cascade_calls == [GROUP]


def test_v3_delete_route_depends_on_the_temporary_gate():
    default = inspect.signature(groups_v3_router.delete_group_v3).parameters["user"].default
    assert default.dependency is compulsory_temporary_token


# ---------------------------------------------------------------------------
# 列宽校验：5 个入口统一 400（不截断）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,method,payload,status",
    [
        (
            "/api/groups",
            "post",
            {"_id": GROUP, "name": "名" * 128, "type": "class", "description": "", "permissions": []},
            200,
        ),
        (
            "/api/groups",
            "post",
            {"_id": GROUP, "name": "名" * 129, "type": "class", "description": "", "permissions": []},
            400,
        ),
        (
            "/api/groups",
            "post",
            {"_id": GROUP, "name": "名" * 10 + "\x00", "type": "class", "description": "", "permissions": []},
            400,
        ),
        (f"/api/groups/{GROUP}/name", "put", {"name": "名" * 129}, 400),
        (f"/api/groups/{GROUP}/name", "put", {"name": "名" * 128}, 200),
        (f"/api/groups/{GROUP}/name", "put", {"name": "名\x00"}, 400),
        (f"/api/groups/{GROUP}/description", "put", {"description": "描" * 1000}, 200),
        (f"/api/groups/{GROUP}/description", "put", {"description": "描" * 1001}, 400),
        (f"/api/groups/{GROUP}/description", "put", {"description": "描\x00"}, 400),
        ("/api/v1/groups", "post", {"name": "名" * 129}, 400),
        ("/api/v1/groups", "post", {"name": "名" * 128}, 201),
        (f"/api/v1/groups/{GROUP}/name", "put", {"name": "名" * 129}, 400),
        (f"/api/v1/groups/{GROUP}/name", "put", {"name": "名" * 128}, 204),
    ],
)
def test_group_column_width_is_validated(monkeypatch, path, method, payload, status):
    repo = _GroupsRepo()
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_v3_router.router, "/api/v1/groups"))
    app.include_router(groups_router.router, prefix="/api/groups")
    app.dependency_overrides[get_current_user] = lambda: ADMIN

    response = TestClient(app).request(method.upper(), path, json=payload)

    assert response.status_code == status, response.text


def test_description_for_missing_group_is_404(monkeypatch):
    repo = _GroupsRepo()

    async def _missing(oid):
        return None

    repo.get = _missing
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_router.router, "/api/groups"))
    app.dependency_overrides[get_current_user] = lambda: ADMIN

    response = TestClient(app).put(
        f"/api/groups/{GROUP}/description", json={"description": "说明"}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 下推：v1 /user 只问一页；v2 /time 认 search 别名
# ---------------------------------------------------------------------------


def test_v1_user_page_pushes_pagination_down(monkeypatch):
    repo = _GroupsRepo()
    repo.member_total = 200
    repo.member_rows = [
        {
            "_id": f"00000000000000000000{index:04d}",
            "id": f"2025{index:04d}",
            "name": f"学生{index}",
            "roles": ["student"],
            "class_id": GROUP,
            "class_name": "甲班",
            "banned": False,
            "password": None,
        }
        for index in range(200)
    ]
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_router.router, "/api/groups"))
    app.dependency_overrides[get_current_user] = lambda: ADMIN

    body = TestClient(app).get(
        f"/api/groups/{GROUP}/user", params={"page": 2, "perpage": 100}
    ).json()

    assert repo.members_calls == [
        {"group_oid": GROUP, "query": "", "page": 2, "size": 100}
    ]
    # 第二页只回一页的行，但 size 是整班总数。
    assert [row["id"] for row in body["data"]] == [
        f"2025{index:04d}" for index in range(100, 200)
    ]
    assert body["metadata"]["size"] == 200


@pytest.mark.parametrize("page", [1, 5])
def test_v1_user_page_handles_empty_and_out_of_range(monkeypatch, page):
    repo = _GroupsRepo()
    _install(monkeypatch, groups_repo=repo)
    app = _client((groups_router.router, "/api/groups"))
    app.dependency_overrides[get_current_user] = lambda: ADMIN

    body = TestClient(app).get(
        f"/api/groups/{GROUP}/user", params={"page": page, "perpage": 10}
    ).json()

    assert body["data"] == []
    assert body["metadata"]["size"] == 0


def test_v1_time_page_uses_the_pushed_down_repo(monkeypatch):
    repo = _GroupsRepo()
    time_repo = _TimeRepo(
        [
            {
                "user": "000000000000000000000021",
                "name": "张三",
                "id": "20250001",
                "on_campus_raw": 30.0,
                "off_campus_raw": 10.0,
                "social_practice": 0.0,
            },
            {
                "user": "000000000000000000000022",
                "name": "李四",
                "id": "20250002",
                "on_campus_raw": 0.0,
                "off_campus_raw": 0.0,
                "social_practice": 0.0,
            },
        ]
    )
    _install(monkeypatch, groups_repo=repo, time_repo=time_repo)
    app = _client((groups_router.router, "/api/groups"))
    app.dependency_overrides[get_current_user] = lambda: ADMIN

    body = TestClient(app).get(
        f"/api/groups/{GROUP}/time",
        params={"page": 2, "perpage": 10, "exceeding": False, "search": "张"},
    ).json()

    assert time_repo.calls == [
        {
            "class_oid": GROUP,
            "query": "张",
            "sort_field": None,
            "descending": False,
            "page": 2,
            "size": 10,
        }
    ]
    assert [row["name"] for row in body["data"]] == ["张三"]
    assert body["metadata"]["size"] == 2


@pytest.mark.parametrize("param", ["search", "query"])
def test_v2_group_time_accepts_search_and_query(monkeypatch, param):
    time_repo = _TimeRepo(
        [
            {
                "user": "000000000000000000000021",
                "name": "张三",
                "id": "20250001",
                "on_campus_raw": 1.0,
                "off_campus_raw": 0.0,
                "social_practice": 0.0,
            },
            {
                "user": "000000000000000000000022",
                "name": "李四",
                "id": "20250002",
                "on_campus_raw": 2.0,
                "off_campus_raw": 0.0,
                "social_practice": 0.0,
            },
        ]
    )
    _install(monkeypatch, time_repo=time_repo)
    app = _client((groups_v2_router.router, "/api/v1/groups"))
    app.dependency_overrides[get_current_user] = lambda: ADMIN

    response = TestClient(app).get(
        f"/api/v1/groups/{GROUP}/time", params={param: "张三"}
    )

    assert response.status_code == 200
    assert time_repo.calls[0]["query"] == "张三"
    assert [row["name"] for row in response.json()["data"]] == ["张三"]


def test_delete_cascade_is_wired_into_the_protocol():
    from db.repos.protocols.groups import GroupsRepo

    assert callable(GroupsRepo.delete_cascade)
