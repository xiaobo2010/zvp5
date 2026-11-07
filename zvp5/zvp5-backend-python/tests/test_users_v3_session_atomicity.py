"""``PUT /api/v1/users/{user_id}/bindings`` 的事务边界。

钉住两件事：

* 改绑（撤销旧 active 绑定 + 激活新绑定）与审计日志在**同一个事务**里提交：
  改绑成功而审计丢失的窗口不存在；
* 任一步失败则整笔回滚——旧绑定仍是 active，日志一条都不留。

仓储用同一份内存 ``data`` 打桩，``session.begin()`` 只在成功退出时把副本写回；
仓储方法都断言「本 session 正好在事务中」，从而把「偷偷另开 session」暴露成失败。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy

import pytest

from db import backends
from routers import users_v3_router as users_v3

ADMIN_OID = "000000000000000000000001"
TARGET_OID = "000000000000000000000002"
OLD_XUEHAI = "20260001"
NEW_XUEHAI = "20260002"


class _Store:
    def __init__(self) -> None:
        self.data = {
            "users": {
                TARGET_OID: {"_id": TARGET_OID, "id": "20240002", "name": "张三"},
                ADMIN_OID: {"_id": ADMIN_OID, "id": "20240001", "name": "王管理"},
            },
            "bindings": {(TARGET_OID, OLD_XUEHAI): "active"},
            "logs": [],
        }
        self.commits = 0
        self.session_opens = 0


class _Session:
    """最小事务外壳：``begin()`` 成功才把改动合并回 store。"""

    def __init__(self, store: _Store, fail_commit: bool = False) -> None:
        self.store = store
        self.pending = None
        self.in_transaction = False
        self.fail_commit = fail_commit

    @property
    def data(self):
        if self.pending is None:
            self.pending = deepcopy(self.store.data)
        return self.pending

    @asynccontextmanager
    async def begin(self):
        self.in_transaction = True
        try:
            yield
        except BaseException:
            self.pending = None
            raise
        else:
            if self.fail_commit:
                raise RuntimeError("commit failed")
            self.store.data = self.pending
            self.pending = None
            self.store.commits += 1
        finally:
            self.in_transaction = False


class _UsersRepo:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def get_by_oid(self, oid: str):
        assert self.session.in_transaction, "user read escaped the handler transaction"
        return self.session.data["users"].get(str(oid))

    async def get_names(self, oids):
        assert self.session.in_transaction, "name read escaped the handler transaction"
        return {
            str(oid): self.session.data["users"][str(oid)]["name"]
            for oid in oids
            if str(oid) in self.session.data["users"]
        }


class _BindingsRepo:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def list_for_user(self, user_oid: str):
        assert self.session.in_transaction
        return [
            {"xuehai_id": xuehai_id, "status": status}
            for (oid, xuehai_id), status in self.session.data["bindings"].items()
            if oid == str(user_oid)
        ]

    async def revoke(self, user_oid: str, xuehai_id: str):
        assert self.session.in_transaction
        self.session.data["bindings"][(str(user_oid), xuehai_id)] = "revoked"

    async def upsert(self, user_oid: str, xuehai_id: str, source: str, note):
        assert self.session.in_transaction
        if xuehai_id == "boom":
            raise RuntimeError("upsert failed")
        self.session.data["bindings"][(str(user_oid), xuehai_id)] = "active"


class _Log:
    def __init__(self, fail: bool = False) -> None:
        self.texts: list[str] = []
        self.fail = fail

    def with_text(self, text: str, type: str = "", level: str = "") -> "_Log":
        self.texts.append(text)
        return self

    async def insert_log(self, session=None):
        assert session is not None, "audit log must join the handler transaction"
        assert session.in_transaction, "audit log escaped the handler transaction"
        if self.fail:
            raise RuntimeError("audit insert failed")
        session.data["logs"].append(self.texts[-1])


@pytest.fixture
def store(monkeypatch):
    state = _Store()

    @asynccontextmanager
    async def scope():
        state.session_opens += 1
        yield _Session(state)

    async def verify(_token, _user):
        return {}

    monkeypatch.setattr(users_v3, "session_scope", scope)
    monkeypatch.setattr(users_v3, "require_password_verification", verify)
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: _UsersRepo(session)
    )
    monkeypatch.setattr(
        backends, "get_bindings_repo", lambda session=None: _BindingsRepo(session)
    )
    return state


ADMIN = {"id": ADMIN_OID, "per": ["admin"]}


@pytest.mark.asyncio
async def test_binding_change_and_audit_commit_together(store) -> None:
    log = _Log()

    await users_v3.set_user_binding_v3(
        TARGET_OID,
        users_v3.PutBindingRequest(xuehai_id=NEW_XUEHAI, note="换平板"),
        user=ADMIN,
        log=log,
    )

    assert store.commits == 1
    assert store.session_opens == 1
    assert store.data["bindings"][(TARGET_OID, OLD_XUEHAI)] == "revoked"
    assert store.data["bindings"][(TARGET_OID, NEW_XUEHAI)] == "active"
    assert store.data["logs"] == [
        f"管理员 王管理 为用户 张三 绑定了学海设备 {NEW_XUEHAI}"
    ]


@pytest.mark.asyncio
async def test_write_failure_rolls_back_the_whole_binding_change(store) -> None:
    log = _Log()

    with pytest.raises(RuntimeError):
        await users_v3.set_user_binding_v3(
            TARGET_OID,
            users_v3.PutBindingRequest(xuehai_id="boom"),
            user=ADMIN,
            log=log,
        )

    assert store.commits == 0
    assert store.data["bindings"] == {(TARGET_OID, OLD_XUEHAI): "active"}
    assert store.data["logs"] == []


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_the_binding_change(store) -> None:
    """审计写不进去时，改绑也不许落库——否则正是「写已提交、审计丢了」那个窗口。"""
    log = _Log(fail=True)

    with pytest.raises(RuntimeError):
        await users_v3.set_user_binding_v3(
            TARGET_OID,
            users_v3.PutBindingRequest(xuehai_id=NEW_XUEHAI),
            user=ADMIN,
            log=log,
        )

    assert store.commits == 0
    assert store.data["bindings"] == {(TARGET_OID, OLD_XUEHAI): "active"}
    assert store.data["logs"] == []
