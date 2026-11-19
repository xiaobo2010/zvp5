"""v4.2.4-2 L3A：身份校验 + 学号修改 + 用户硬删除/回收站/GC。

覆盖契约 §3.3A 的四条后端能力：

* ``PUT /api/v1/users/{id}/roles`` 的**目标侧保护**（禁改其他 admin / 禁改自己）；
* 新增 ``PUT /api/v1/users/{id}/student-id``（轻校验 / 409 / 轮换 session_id）；
* 新增 ``DELETE /api/v1/users/{id}``（单事务归档 + 清理 + 删行，失败整体回滚）；
* ``tasks/user_trash_gc.py``（30 天保留期、可配、只清过期行、写审计）。

离线方式（与 ``tests/test_retention_async.py`` 同一风格：同步用例内
``asyncio.run``，不依赖 asyncio 插件模式）：

* HTTP 层用 ``TestClient`` + 依赖覆盖 + 替身仓库；
* 删除流程用替身会话 ``FakeSession`` 驱动**真实**的
  ``db.repos.oracle.users.archive_and_delete_user`` 语句——替身把写入先落在
  staged 副本上、只在 ``commit()`` 时并回调用方持有的字典，因此「失败 ⇒ 未
  commit ⇒ 一行都没落库」可以直接断言；
* 本机没有 aiosqlite，也没有生产库访问权限，所以这里不跑真 SQLite/Oracle
  （见报告「未决项」）。

跑法：``.venv/bin/python -m pytest tests/test_v4242_user_admin.py -q``
"""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import oracle

from db import backends
from db.ids import is_valid_oid, new_oid
from db.repos.oracle import users as users_repo
from db.repos.oracle.users import (
    UserGoneError,
    _FILES_METADATA_COLUMNS,
    _purge_trash_stmt,
    _set_business_id_stmt,
    _set_roles_stmt,
    OracleUsersRepo,
)
from routers import users_router
from tasks import user_trash_gc
from typings.log import inject_log
from util.cert import jwt_encode
from util.object_id import get_current_user
from util.permission.user import role_change_forbidden

SELF = "000000000000000000000001"
ADMIN = "000000000000000000000003"
OTHER = "000000000000000000000002"


def _dialect():
    return oracle.dialect()


def _params(stmt):
    return stmt.compile(dialect=_dialect()).params


def _valid_student_id(seat: int) -> str:
    """当前学年起点的「平台常见格式」学号，只用来给替身用户造互不冲突的编号。

    学号格式闸已经移除（改学号端点与建号共用轻校验），这里保留这个形状只是让
    用例数据看起来像真的学号；唯一性仍是必须满足的条件。
    """
    now = datetime.now()
    year = now.year if now.month >= 8 else now.year - 1
    return f"{year:04d}01{seat:02d}"


# ---------------------------------------------------------------------------
# HTTP 层替身
# ---------------------------------------------------------------------------


class StubLog:
    def __init__(self):
        self.calls = []

    def with_text(self, text, type="", level=""):
        self.calls.append({"text": text, "type": type, "level": level})
        return self

    async def insert_log(self):
        return None


class StubUsersRepo:
    """记录所有写调用；读调用从内存字典取。"""

    def __init__(self, users, fail_delete=False):
        self.users = users
        self.fail_delete = fail_delete
        self.set_roles_calls = []
        self.set_business_id_calls = []
        self.delete_calls = []

    async def get_by_oid(self, oid):
        return self.users.get(oid)

    async def find_by_business_id(self, business_id):
        for doc in self.users.values():
            if doc["id"] == business_id:
                return doc
        return None

    async def set_roles(self, oid, roles):
        self.set_roles_calls.append((oid, list(roles)))

    async def set_business_id(self, oid, business_id):
        self.set_business_id_calls.append((oid, business_id))

    async def delete_user_cascade(self, oid, deleted_by):
        self.delete_calls.append((oid, deleted_by))
        if self.fail_delete:
            raise UserGoneError("gone")


@pytest.fixture
def env(monkeypatch):
    ids = {seat: _valid_student_id(seat) for seat in (1, 2, 3, 4)}
    users = {
        SELF: {"_id": SELF, "id": ids[1], "name": "自己", "roles": ["admin"]},
        ADMIN: {"_id": ADMIN, "id": ids[2], "name": "另一个管理员", "roles": ["admin"]},
        OTHER: {"_id": OTHER, "id": ids[3], "name": "同学", "roles": ["student"]},
    }
    repo = StubUsersRepo(users)

    async def display_name(oid):
        return "操作者"

    log = StubLog()
    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: repo)
    monkeypatch.setattr(users_router, "get_user_display_name", display_name)

    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1/users")
    app.dependency_overrides[get_current_user] = lambda: {"id": SELF, "per": ["admin"]}
    app.dependency_overrides[inject_log] = lambda: log

    return SimpleNamespace(
        client=TestClient(app),
        app=app,
        repo=repo,
        log=log,
        ids=ids,
        new_student_id=ids[4],
    )


def _verify_token(oid: str) -> str:
    """密码复核签发的 5 分钟短令牌（硬删除要求「是本人坐在这」）。"""
    return jwt_encode(oid, [], [], "short", session_id="", purpose="verify")


def _login_as(env, per, oid=SELF):
    env.app.dependency_overrides[get_current_user] = lambda: {"id": oid, "per": list(per)}


# ---------------------------------------------------------------------------
# 1. 身份：目标侧保护
# ---------------------------------------------------------------------------


def test_role_change_forbidden_is_pure_policy():
    assert role_change_forbidden(SELF, ["admin"], SELF) is True  # 改自己
    assert role_change_forbidden(SELF, ["admin"], ADMIN) is True  # 改别的 admin
    assert role_change_forbidden(SELF, ["student"], OTHER) is False
    assert role_change_forbidden(SELF, [], OTHER) is False


def test_admin_cannot_change_other_admin_roles(env):
    response = env.client.put(f"/api/v1/users/{ADMIN}/roles", json={"roles": ["student"]})
    assert response.status_code == 403
    assert response.json()["detail"] == "不能修改管理员账号的角色"
    assert env.repo.set_roles_calls == []
    assert env.log.calls == []


def test_admin_cannot_change_own_roles(env):
    """自我提权 / 自我降级（自锁）都在同一条闸下被拒，且不落库、不写审计。"""
    for roles in (["admin"], ["student"], ["admin", "department"]):
        response = env.client.put(f"/api/v1/users/{SELF}/roles", json={"roles": roles})
        assert response.status_code == 403
        assert response.json()["detail"] == "不能修改自己的角色"
    assert env.repo.set_roles_calls == []
    assert env.log.calls == []


def test_non_admin_cannot_change_roles(env):
    _login_as(env, ["student"])
    response = env.client.put(f"/api/v1/users/{OTHER}/roles", json={"roles": ["admin"]})
    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"
    assert env.repo.set_roles_calls == []


def test_unknown_role_is_rejected_by_whitelist(env):
    response = env.client.put(f"/api/v1/users/{OTHER}/roles", json={"roles": ["superuser"]})
    assert response.status_code == 422
    assert env.repo.set_roles_calls == []


def test_admin_can_change_other_non_admin_roles(env):
    response = env.client.put(f"/api/v1/users/{OTHER}/roles", json={"roles": ["secretary"]})
    assert response.status_code == 204
    assert env.repo.set_roles_calls == [(OTHER, ["secretary"])]
    assert env.log.calls[0]["type"] == "role_change"


def test_role_change_missing_target_is_404(env):
    response = env.client.put(
        "/api/v1/users/0000000000000000000000ff/roles", json={"roles": ["student"]}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 2. 学号
# ---------------------------------------------------------------------------


def test_student_id_success_writes_audit(env):
    response = env.client.put(
        f"/api/v1/users/{OTHER}/student-id", json={"id": env.new_student_id}
    )
    assert response.status_code == 204
    assert env.repo.set_business_id_calls == [(OTHER, env.new_student_id)]
    assert env.log.calls[0]["type"] == "student_id_change"


def test_student_id_unusual_format_is_accepted(env):
    """学号格式闸已移除：非常规形状（非 8 位 / 2019 年段 / 含字母）都放行。

    班级内建号的编号规则由班级自定，改学号端点必须与建号口同口径——否则会
    「建得出来却改不回去」。
    """
    for unusual in ("2019", "201901011", "abcdefgh", "2019-1-01", "cs2019A"):
        response = env.client.put(
            f"/api/v1/users/{OTHER}/student-id", json={"id": unusual}
        )
        assert response.status_code == 204, unusual
    assert [call[1] for call in env.repo.set_business_id_calls] == [
        "2019",
        "201901011",
        "abcdefgh",
        "2019-1-01",
        "cs2019A",
    ]
    assert env.log.calls[-1]["type"] == "student_id_change"


def test_student_id_is_stripped_before_write(env):
    """首尾空白是输入噪音，strip 后再落库（登录名不该带不可见字符）。"""
    response = env.client.put(
        f"/api/v1/users/{OTHER}/student-id", json={"id": "  cs2019A  "}
    )
    assert response.status_code == 204
    assert env.repo.set_business_id_calls == [(OTHER, "cs2019A")]


def test_student_id_blank_is_400(env):
    for blank in ("", "   ", "\t\n"):
        response = env.client.put(f"/api/v1/users/{OTHER}/student-id", json={"id": blank})
        assert response.status_code == 400, blank
        assert response.json()["detail"] == "学号不能为空"
    assert env.repo.set_business_id_calls == []
    assert env.log.calls == []


def test_student_id_too_long_is_400(env):
    response = env.client.put(
        f"/api/v1/users/{OTHER}/student-id", json={"id": "a" * 33}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "学号长度不能超过 32 个字符"
    assert env.repo.set_business_id_calls == []

    # 边界：正好 32 字符是列宽上限，必须放行（否则等于把 33 的错算到 32 头上）。
    response = env.client.put(
        f"/api/v1/users/{OTHER}/student-id", json={"id": "b" * 32}
    )
    assert response.status_code == 204


def test_student_id_with_space_or_control_char_is_400(env):
    for bad in ("2019 01", "2019\u000101", "2019\u007f01"):
        response = env.client.put(f"/api/v1/users/{OTHER}/student-id", json={"id": bad})
        assert response.status_code == 400, repr(bad)
        assert response.json()["detail"] == "学号不能包含空白或控制字符"
    assert env.repo.set_business_id_calls == []
    assert env.log.calls == []


def test_student_id_duplicate_is_409(env):
    """学号唯一：占用者是别人 → 409（自己占用自己是幂等改写，不放行重复）。"""
    response = env.client.put(f"/api/v1/users/{OTHER}/student-id", json={"id": env.ids[1]})
    assert response.status_code == 409
    assert response.json()["detail"] == "学号已存在"
    assert env.repo.set_business_id_calls == []


def test_student_id_non_admin_is_403(env):
    _login_as(env, ["department"])
    response = env.client.put(
        f"/api/v1/users/{OTHER}/student-id", json={"id": env.new_student_id}
    )
    assert response.status_code == 403
    assert env.repo.set_business_id_calls == []


def test_student_id_unknown_user_is_404(env):
    response = env.client.put(
        "/api/v1/users/0000000000000000000000ff/student-id",
        json={"id": env.new_student_id},
    )
    assert response.status_code == 404


def test_set_business_id_stmt_rotates_session():
    """学号 = 登录名：写语句必须同时轮换 session_id（旧 token 立即失效）。"""
    params = _params(_set_business_id_stmt(OTHER, "20240101"))
    assert params["business_id"] == "20240101"
    assert params["oid_1"] == OTHER
    assert is_valid_oid(params["session_id"])


def test_set_roles_stmt_rotates_session():
    params = _params(_set_roles_stmt(OTHER, ["secretary"]))
    assert is_valid_oid(params["session_id"])


# ---------------------------------------------------------------------------
# 3. 删除用户（HTTP 层）
# ---------------------------------------------------------------------------


def test_cannot_delete_self(env):
    response = env.client.request(
        "DELETE",
        f"/api/v1/users/{SELF}",
        json={"verification_token": _verify_token(SELF)},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "不能删除自己"
    assert env.repo.delete_calls == []
    assert env.log.calls == []


def test_non_admin_cannot_delete(env):
    _login_as(env, ["department"])
    response = env.client.delete(f"/api/v1/users/{OTHER}")
    assert response.status_code == 403
    assert env.repo.delete_calls == []


def test_delete_user_writes_warning_audit(env):
    response = env.client.request(
        "DELETE",
        f"/api/v1/users/{OTHER}",
        json={"verification_token": _verify_token(SELF)},
    )
    assert response.status_code == 204
    assert env.repo.delete_calls == [(OTHER, SELF)]
    audit = env.log.calls[0]
    assert audit["type"] == "user_hard_delete"
    assert audit["level"] == "warning"
    assert "回收站" in audit["text"]


def test_delete_user_gone_is_404_and_not_logged(env):
    env.repo.fail_delete = True
    response = env.client.request(
        "DELETE",
        f"/api/v1/users/{OTHER}",
        json={"verification_token": _verify_token(SELF)},
    )
    assert response.status_code == 404
    assert env.log.calls == []


def test_delete_missing_user_is_404(env):
    response = env.client.delete("/api/v1/users/0000000000000000000000ff")
    assert response.status_code == 404
    assert env.repo.delete_calls == []


# ---------------------------------------------------------------------------
# 3b. 删除用户（仓库层：单事务归档 + 清理 + 删行）
# ---------------------------------------------------------------------------


class FakeSession:
    """最小事务替身。

    ``execute`` 只按语句类型 + 目标表分发，并用编译出的绑定参数做等值过滤；
    足够驱动真实的 ``archive_and_delete_user``（读 users/子表 → 写 user_trash →
    删带外键子表 → 删 users）。写入落在 ``staged``，只有 ``commit()`` 才并回调用
    方持有的 ``origin`` 字典——这就是「未提交 = 一行都没落库」的可断言形式。
    """

    def __init__(self, origin, fail_on=None):
        self.origin = origin
        self.staged = copy.deepcopy(origin)
        self.fail_on = fail_on
        self.commits = 0
        self.closed = False
        self.log = []

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _table_of(stmt):
        table = getattr(stmt, "table", None)
        if table is not None:
            return table.name
        get_final_froms = getattr(stmt, "get_final_froms", None)
        if get_final_froms is None:
            # TextClause（如 user_tables 的存在性探测）没有 FROM 可枚举。
            return None
        for frm in get_final_froms():
            name = getattr(frm, "name", None)
            if name:
                return name
        return None

    @staticmethod
    def _cond(stmt):
        params = _params(stmt)
        if not params:
            return None, None
        key = next(iter(params))
        return key.rsplit("_", 1)[0], params[key]

    def _select(self, stmt):
        rows = self.staged.get(self._table_of(stmt), [])
        column, value = self._cond(stmt)
        if column is not None:
            rows = [row for row in rows if row.get(column) == value]
        return rows

    # -- session API ---------------------------------------------------
    async def execute(self, stmt):
        kind = type(stmt).__name__.lower()
        table = self._table_of(stmt)
        self.log.append((kind, table))

        if kind == "textclause":
            # schema 漂移探测：仓库真实实现会先问 user_tables 有哪些子表。
            sql = str(stmt)
            if "USER_TABLES" not in sql.upper():
                raise AssertionError(f"unexpected text statement: {sql}")
            requested = sql.split("'")[1::2]
            present = [name for name in requested if name.lower() in self.staged]
            return SimpleNamespace(
                all=lambda: [(name,) for name in present],
                first=lambda: None,
                scalar_one=lambda: len(present),
                rowcount=len(present),
            )

        if kind == "select":
            rows = self._select(stmt)
            return SimpleNamespace(
                first=(lambda: SimpleNamespace(_mapping=rows[0])) if rows else (lambda: None),
                all=lambda: [SimpleNamespace(_mapping=row) for row in rows],
                scalar_one=lambda: len(rows),
                rowcount=len(rows),
            )

        if kind == "insert":
            self.staged.setdefault(table, []).append(dict(_params(stmt)))
            return SimpleNamespace(rowcount=1)

        if kind == "delete":
            if self.fail_on == table:
                raise RuntimeError(f"simulated DB failure while deleting {table}")
            column, value = self._cond(stmt)
            rows = self.staged.get(table, [])
            kept = [row for row in rows if row.get(column) != value]
            removed = len(rows) - len(kept)
            self.staged[table] = kept
            return SimpleNamespace(rowcount=removed)

        raise AssertionError(f"unexpected statement: {type(stmt)!r}")

    async def commit(self):
        self.commits += 1
        committed = copy.deepcopy(self.staged)
        self.origin.clear()
        self.origin.update(committed)

    async def close(self):
        self.closed = True


def _fake_db():
    old = datetime(2026, 1, 1, 12, 0, 0)
    return {
        "users": [
            {
                "oid": OTHER,
                "business_id": "20230103",
                "name": "同学",
                "roles": ["student"],
                "class_oid": None,
                "password_hash": "x",
                "session_id": "s",
                "sex": None,
                "eligibility": None,
                "past": None,
                "banned": 0,
            }
        ],
        "user_time": [
            {
                "oid": new_oid(),
                "user_oid": OTHER,
                "on_campus_raw": 8.5,
                "off_campus_raw": 0.0,
                "social_practice": 0.0,
                "updated_at": old,
            }
        ],
        "time_academic_year": [
            {
                "oid": new_oid(),
                "user_oid": OTHER,
                "entry_year": "2023",
                "academic_year": 2026,
                "start_of_year": old,
                "end_of_year": old,
                "on_campus": Decimal("8.5"),
                "off_campus": 0.0,
                "social_practice": 0.0,
            }
        ],
        "time_with_origin": [
            {"oid": new_oid(), "user_oid": OTHER, "data": {"labor": 8.5}, "updated_at": old}
        ],
        "user_device_bindings": [
            {
                "oid": new_oid(),
                "user_oid": OTHER,
                "xuehai_id": "1001",
                "device_id": None,
                "source": "auto",
                "status": "active",
                "note": None,
                "created_at": old,
                "updated_at": old,
            }
        ],
        "ban_records": [
            {
                "oid": new_oid(),
                "user_oid": OTHER,
                "source": "rule",
                "rule_code": "multi_device_login",
                "reason": "同学号多设备登录",
                "permanent": 0,
                "started_at": old,
                "expires_at": old,
                "operator_oid": None,
                "created_at": old,
            }
        ],
        "activity_members": [
            {
                "oid": new_oid(),
                "activity_oid": new_oid(),
                "member_oid": OTHER,
                "member_status": "effective",
                "member_mode": "on-campus",
                "duration_hours": 8.5,
            }
        ],
        "files": [
            {
                "oid": new_oid(),
                "owner_oid": OTHER,
                "kind": "original",
                "mime": "image/png",
                "size_bytes": 123,
                "sha256": "deadbeef",
                "status": "ready",
                "created_at": old,
                "deleted_at": None,
            }
        ],
        "user_trash": [],
    }


@pytest.fixture
def install_fake_session(monkeypatch):
    """替换 repos 模块里的 session_scope，让 delete_user_cascade 用替身会话。"""

    def _install(db=None, fail_on=None):
        origin = db if db is not None else _fake_db()
        session = FakeSession(origin, fail_on=fail_on)

        @asynccontextmanager
        async def scope():
            yield session

        monkeypatch.setattr(users_repo, "session_scope", scope)
        return session

    return _install


def test_delete_cascade_archives_and_cleans_in_one_transaction(install_fake_session):
    db = _fake_db()
    session = install_fake_session(db=db)

    trash_oid = asyncio.run(OracleUsersRepo().delete_user_cascade(OTHER, SELF))

    # 一次事务、一次提交，回收站行的 oid 被返回。
    assert session.commits == 1
    assert is_valid_oid(trash_oid)

    # 带外键的子表被物理清理；users 行被删。
    assert db["users"] == []
    for table in (
        "user_time",
        "time_academic_year",
        "time_with_origin",
        "user_device_bindings",
        "ban_records",
    ):
        assert db[table] == [], table

    # 软引用按决策保留：activity_members / files 行原地不动（owner_oid 也保留）。
    assert len(db["activity_members"]) == 1
    assert len(db["files"]) == 1
    assert db["files"][0]["owner_oid"] == OTHER
    assert db["activity_members"][0]["member_oid"] == OTHER

    # 回收站有记录，payload 是整包快照。
    assert len(db["user_trash"]) == 1
    trash = db["user_trash"][0]
    assert trash["oid"] == trash_oid
    assert trash["user_oid"] == OTHER
    assert trash["deleted_by"] == SELF

    payload = json.loads(trash["payload"])
    assert payload["user"]["business_id"] == "20230103"
    assert payload["user"]["roles"] == ["student"]
    assert payload["related"]["user_time"][0]["on_campus_raw"] == 8.5
    assert payload["related"]["user_device_bindings"][0]["xuehai_id"] == "1001"
    assert payload["related"]["ban_records"][0]["rule_code"] == "multi_device_login"
    assert payload["related"]["activity_members"][0]["member_oid"] == OTHER
    assert payload["related"]["files"][0]["sha256"] == "deadbeef"

    # JSON 列必须保形（list/dict 原样进 payload，而不是被 str() 成字符串）。
    assert payload["user"]["roles"] == ["student"]
    assert payload["related"]["time_with_origin"][0]["data"] == {"labor": 8.5}

    # TIMESTAMP / NUMBER 被压成 JSON 安全值。
    assert payload["related"]["user_time"][0]["updated_at"] == "2026-01-01T12:00:00"
    assert payload["related"]["time_academic_year"][0]["on_campus"] == 8.5

    # BLOB 绝不进 payload：查询只投影元数据列，_json_safe 也会丢弃 bytes。
    assert "content" not in payload["related"]["files"][0]
    assert "content" not in {column.name for column in _FILES_METADATA_COLUMNS}


def test_delete_cascade_clears_ban_records_before_the_users_row(install_fake_session):
    """BAN_RECORDS 没有 ON DELETE CASCADE：不清子表就删 users 会 ORA-02292。"""
    db = _fake_db()
    session = install_fake_session(db=db)

    asyncio.run(OracleUsersRepo().delete_user_cascade(OTHER, SELF))

    deleted = [table for kind, table in session.log if kind == "delete"]
    assert "ban_records" in deleted
    assert deleted.index("ban_records") < deleted.index("users")
    assert db["ban_records"] == []


def test_delete_cascade_skips_tables_missing_from_the_live_schema(install_fake_session):
    """schema 漂移回归：线上缺表（实测缺 user_device_bindings）不能让删除整条挂掉。"""
    db = _fake_db()
    db.pop("user_device_bindings")
    session = install_fake_session(db=db)

    trash_oid = asyncio.run(OracleUsersRepo().delete_user_cascade(OTHER, SELF))

    assert session.commits == 1
    assert is_valid_oid(trash_oid)
    assert db["users"] == []
    assert db["user_time"] == []
    assert "user_device_bindings" not in db

    payload = json.loads(db["user_trash"][0]["payload"])
    assert payload["skipped_tables"] == ["user_device_bindings"]
    assert "user_device_bindings" not in payload["related"]
    # 其余子表照常归档，不被缺表连坐。
    assert payload["related"]["user_time"][0]["on_campus_raw"] == 8.5
    assert payload["related"]["activity_members"][0]["member_oid"] == OTHER


def test_delete_cascade_rolls_back_entirely_on_failure(install_fake_session):
    """删除 users 行时失败 ⇒ 没有 commit ⇒ 归档/清理全部不落库（无半删状态）。"""
    db = _fake_db()
    session = install_fake_session(db=db, fail_on="users")

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        asyncio.run(OracleUsersRepo().delete_user_cascade(OTHER, SELF))

    assert session.commits == 0
    # 库里一切都还在：users 行 + 四张带外键子表 + 没有留下「已归档但人还在」的
    # 孤儿回收站记录。
    assert db["users"][0]["oid"] == OTHER
    assert len(db["user_time"]) == 1
    assert len(db["time_academic_year"]) == 1
    assert len(db["time_with_origin"]) == 1
    assert len(db["user_device_bindings"]) == 1
    assert db["user_trash"] == []


def test_delete_cascade_rolls_back_when_child_cleanup_fails(install_fake_session):
    """清理子表阶段失败同样整体回滚（先写入的归档语句也不落库）。"""
    db = _fake_db()
    session = install_fake_session(db=db, fail_on="user_device_bindings")

    with pytest.raises(RuntimeError):
        asyncio.run(OracleUsersRepo().delete_user_cascade(OTHER, SELF))

    assert session.commits == 0
    assert db["user_time"] and db["user_device_bindings"]
    assert db["user_trash"] == []


def test_delete_cascade_unknown_user_raises_gone(install_fake_session):
    db = _fake_db()
    db["users"] = []
    session = install_fake_session(db=db)

    with pytest.raises(UserGoneError):
        asyncio.run(OracleUsersRepo().delete_user_cascade(OTHER, SELF))

    assert session.commits == 0
    assert db["user_trash"] == []


# ---------------------------------------------------------------------------
# 4. 回收站 GC
# ---------------------------------------------------------------------------


class StubTrashRepo:
    """带保留期过滤的内存替身：只按传入的 cutoff 判过期。"""

    def __init__(self, deleted_at_list):
        self.rows = [{"deleted_at": moment} for moment in deleted_at_list]

    async def purge_trash_batch(self, cutoff, batch_size):
        expired = [row for row in self.rows if row["deleted_at"] < cutoff]
        take = expired[:batch_size]
        for row in take:
            self.rows.remove(row)
        return len(take)

    async def count_expired_trash(self, cutoff):
        return len([row for row in self.rows if row["deleted_at"] < cutoff])


class StubAppLogsRepo:
    def __init__(self):
        self.rows = []

    async def insert(
        self, url, user, data, timestamp, ltype, level, xuehai_id,
        client_ip="", user_agent="",
    ):
        self.rows.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "timestamp": timestamp,
                "ltype": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
                "client_ip": client_ip,
                "user_agent": user_agent,
            }
        )


@pytest.fixture
def gc_env(monkeypatch):
    def _install(deleted_at_list, **config_overrides):
        trash = StubTrashRepo(deleted_at_list)
        logs = StubAppLogsRepo()
        monkeypatch.setattr(backends, "get_users_repo", lambda session=None: trash)
        monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: logs)
        for key, value in config_overrides.items():
            monkeypatch.setattr(user_trash_gc.config, key, value, raising=False)
        return trash, logs

    return _install


def test_purge_stmt_targets_deleted_at_with_rownum():
    cutoff = datetime(2026, 1, 1)
    stmt = _purge_trash_stmt(cutoff, 10)
    sql = str(stmt.compile(dialect=_dialect()))
    assert "user_trash.deleted_at <" in sql
    assert "ROWNUM <= " in sql
    params = _params(stmt)
    assert cutoff in params.values() and 10 in params.values()
    # 保留期只由 deleted_at 决定（restored_at 已于 V052 删除），SQL 里不应出现它。
    assert "restored_at" not in sql


def test_retention_days_defaults_and_overrides(monkeypatch):
    monkeypatch.setattr(user_trash_gc.config, "USER_TRASH_RETENTION_DAYS", 30, raising=False)
    assert user_trash_gc.trash_retention_days() == 30
    monkeypatch.setattr(user_trash_gc.config, "USER_TRASH_RETENTION_DAYS", 5, raising=False)
    assert user_trash_gc.trash_retention_days() == 5
    monkeypatch.setattr(user_trash_gc.config, "USER_TRASH_RETENTION_DAYS", "oops", raising=False)
    assert user_trash_gc.trash_retention_days() == 30


def test_gc_purges_only_rows_older_than_30_days(gc_env):
    now = datetime.now()
    trash, logs = gc_env(
        [now - timedelta(days=40), now - timedelta(days=31), now - timedelta(days=1)]
    )

    deleted = asyncio.run(user_trash_gc.purge_expired_trash())

    assert deleted == 2
    assert len(trash.rows) == 1
    assert trash.rows[0]["deleted_at"] > now - timedelta(days=30)

    assert len(logs.rows) == 1
    audit = logs.rows[0]
    assert audit["ltype"] == "user_trash_gc"
    assert audit["level"] == "warning"
    assert audit["user"] == "scheduler"
    assert "剩余 0 行" in audit["data"]


def test_gc_uses_configured_retention_window(gc_env):
    now = datetime.now()
    trash, _logs = gc_env(
        [now - timedelta(days=6), now - timedelta(days=1)],
        USER_TRASH_RETENTION_DAYS=5,
    )

    assert asyncio.run(user_trash_gc.purge_expired_trash()) == 1
    assert len(trash.rows) == 1


def test_gc_stops_at_max_batches(gc_env):
    now = datetime.now()
    trash, logs = gc_env(
        [now - timedelta(days=40) for _ in range(5)],
        TRASH_GC_BATCH_SIZE=2,
        TRASH_GC_MAX_BATCHES=2,
    )

    result = asyncio.run(user_trash_gc.run_trash_gc())

    assert result["deleted"] == 4  # 2 批 × 2 行，到顶即停
    assert result["remaining"] == 1  # 剩下的留给下一轮
    assert len(trash.rows) == 1
    assert "剩余 1 行" in logs.rows[0]["data"]


def test_gc_failure_is_swallowed(gc_env, monkeypatch):
    """坏库/瞬时错误不能把调度器带崩（与 retention 同一约定）。"""
    gc_env([])

    async def boom(cutoff, batch_size):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: SimpleNamespace(purge_trash_batch=boom)
    )

    assert asyncio.run(user_trash_gc.purge_expired_trash()) == 0
