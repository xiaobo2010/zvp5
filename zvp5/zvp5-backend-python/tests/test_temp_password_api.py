"""一次性临时密码 + 主学海设备自助端点的离线契约。

覆盖两件事：

* ``POST /api/v1/users/{user_id}/temp-password`` 的权限矩阵（admin/department
  全校、secretary 同班、student 拒绝、给 admin 建号要 admin 身份）、返回结构、
  8 位纯数字明文，以及「库里只有 bcrypt 哈希、明文只在响应里出现一次」。
* ``/me/device`` 与 ``/me/primary-device`` 的设备身份闸门：首次绑定必须发生在
  目标设备上，换绑/解绑只能由当前主设备发起。

全部离线：用户/临时密码仓储打桩；``services.device_guard`` 由本文件注入等价
替身（真实实现由服务层车道提供），从而不连 Oracle、也不依赖并行车道的落地时序。
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest
from bcrypt import checkpw, gensalt, hashpw
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services
from db import backends
from routers import users_v3_router
from typings.log import inject_log
from util.cert import jwt_encode
from util.object_id import get_current_user

PREFIX = "/api/v1/users"

ADMIN_OID = "000000000000000000000001"
SECRETARY_OID = "000000000000000000000002"
DEPARTMENT_OID = "000000000000000000000003"
STUDENT_OID = "000000000000000000000004"
MATE_OID = "000000000000000000000005"
OTHER_OID = "000000000000000000000006"
ADMIN_TARGET_OID = "000000000000000000000007"

CLASS_A = "00000000000000000000aa01"
CLASS_B = "00000000000000000000aa02"


def _user(oid, business_id, name, roles, class_id):
    return {
        "_id": oid,
        "id": business_id,
        "name": name,
        "roles": roles,
        "class_id": class_id,
        "primary_xuehai_id": None,
    }


class FakeLog:
    """``inject_log`` 的替身：只记录文本/类型/级别，不落库。"""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.user = ""

    def with_text(self, text: str, type: str = "", level: str = "") -> "FakeLog":
        self.entries.append({"text": text, "type": type, "level": level})
        return self

    async def insert_log(self, session=None) -> None:
        return None


@pytest.fixture
def api(monkeypatch):
    users = {
        ADMIN_OID: _user(ADMIN_OID, "2026001", "管理员", ["admin"], None),
        SECRETARY_OID: _user(SECRETARY_OID, "2026002", "团支书", ["secretary"], CLASS_A),
        DEPARTMENT_OID: _user(
            DEPARTMENT_OID, "2026003", "学生会", ["department"], CLASS_B
        ),
        STUDENT_OID: _user(STUDENT_OID, "2026004", "学生", ["student"], CLASS_A),
        MATE_OID: _user(MATE_OID, "2026005", "同班同学", ["student"], CLASS_A),
        OTHER_OID: _user(OTHER_OID, "2026006", "外班同学", ["student"], CLASS_B),
        ADMIN_TARGET_OID: _user(
            ADMIN_TARGET_OID, "2026007", "另一个管理员", ["admin"], None
        ),
    }
    calls = {
        "inserts": [],
        "set_primary": [],
        "clear_primary": [],
        "ban_delete": [],
        "revoked_open": [],
        "ops": [],
    }

    # 设备封禁替身：默认不封；单个用例可改写 status["ban"]。
    guard = ModuleType("services.device_guard")
    guard.TEMP_PASSWORD_TTL_MINUTES = 30
    guard.TEMP_PASSWORD_CODE_LEN = 8

    # 生成/哈希的唯一实现在服务层；替身保留同样的语义（8 位数字 + bcrypt），
    # 这样端点走服务层函数后仍能验证「库里只有哈希」。
    guard.generate_temp_password = lambda: "12345678"
    guard.hash_temp_password = lambda code: hashpw(
        code.encode("utf-8"), gensalt()
    ).decode("utf-8")

    async def hash_temp_password_async(code):
        return guard.hash_temp_password(code)

    guard.hash_temp_password_async = hash_temp_password_async

    status = {"ban": (False, None, None)}

    async def device_ban_status(xuehai_id):
        return status["ban"]

    guard.device_ban_status = device_ban_status
    monkeypatch.setitem(sys.modules, "services.device_guard", guard)
    monkeypatch.setattr(services, "device_guard", guard, raising=False)

    async def get_by_oid(oid):
        return users.get(str(oid))

    async def get_names(oids):
        return {
            oid: users[oid]["name"] for oid in oids if oid in users
        }

    async def set_primary_xuehai(oid, xuehai_id):
        calls["set_primary"].append((oid, xuehai_id))
        if oid in users:
            users[oid]["primary_xuehai_id"] = xuehai_id

    async def clear_primary_xuehai(oid):
        calls["clear_primary"].append(oid)
        if oid in users:
            users[oid]["primary_xuehai_id"] = None

    async def temp_revoke_open(target_oid, now=None):
        calls["revoked_open"].append((target_oid, now))
        calls["ops"].append("revoke")
        return 1

    async def temp_insert(target_oid, creator_oid, code_hash, creator_xuehai, expires_at):
        calls["ops"].append("insert")
        calls["inserts"].append(
            {
                "target_oid": target_oid,
                "creator_oid": creator_oid,
                "code_hash": code_hash,
                "creator_xuehai_id": creator_xuehai,
                "expires_at": expires_at,
            }
        )
        return "0000000000000000000000ff"

    async def temp_issue(target_oid, creator_oid, code_hash, creator_xuehai, expires_at):
        # Mirrors the real repo: revoke + insert share one transaction.
        await temp_revoke_open(target_oid, datetime.now())
        return await temp_insert(
            target_oid, creator_oid, code_hash, creator_xuehai, expires_at
        )

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_oid=get_by_oid,
            get_names=get_names,
            set_primary_xuehai=set_primary_xuehai,
            clear_primary_xuehai=clear_primary_xuehai,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_temp_passwords_repo",
        lambda *a, **k: SimpleNamespace(
            issue_for_user=temp_issue,
            insert=temp_insert,
            revoke_open_for_user=temp_revoke_open,
        ),
        raising=False,
    )

    async def delete_for_user_rule(oid, rule_code=None):
        calls["ban_delete"].append((oid, rule_code))
        return 3

    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(delete_for_user_rule=delete_for_user_rule),
        raising=False,
    )

    class _AdminSession:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    @asynccontextmanager
    async def admin_scope():
        yield _AdminSession()

    # 封禁梯度重置现在把删除和审计放在同一个事务里；测试替身不需要真库。
    monkeypatch.setattr(users_v3_router, "session_scope", admin_scope)

    log = FakeLog()
    app = FastAPI()
    app.include_router(users_v3_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN_OID,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: log
    return SimpleNamespace(
        http=TestClient(app),
        app=app,
        users=users,
        calls=calls,
        log=log,
        status=status,
    )


def _act_as(api, oid: str, per: list[str]) -> None:
    api.app.dependency_overrides[get_current_user] = lambda: {"id": oid, "per": per}


def _verify_token(oid: str) -> str:
    """密码复核签发的 5 分钟短令牌（与线上同一条 ``jwt_encode`` 链路）。

    短令牌的 ``scope`` 是 ``temporary_token``、``purpose`` 是 ``verify``；``sid``
    用空串，因为本文件的仓储桩里用户没有 ``session_id``。
    """
    return jwt_encode(oid, [], [], "short", session_id="", purpose="verify")


# ---------------------------------------------------------------------------
# POST /{user_id}/temp-password —— 权限矩阵
# ---------------------------------------------------------------------------


def test_secretary_can_create_for_same_class(api) -> None:
    _act_as(api, SECRETARY_OID, ["secretary"])

    response = api.http.post(
        f"{PREFIX}/{MATE_OID}/temp-password",
        json={"verification_token": _verify_token(SECRETARY_OID)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    data = body["data"]
    assert set(data) == {"code", "expires_at", "target"}
    assert data["target"] == {
        "_id": MATE_OID,
        "id": "2026005",
        "name": "同班同学",
    }


def test_secretary_cross_class_is_forbidden(api) -> None:
    _act_as(api, SECRETARY_OID, ["secretary"])

    response = api.http.post(f"{PREFIX}/{OTHER_OID}/temp-password")

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"
    assert api.calls["inserts"] == []


def test_department_covers_whole_school(api) -> None:
    _act_as(api, DEPARTMENT_OID, ["department"])

    response = api.http.post(
        f"{PREFIX}/{OTHER_OID}/temp-password",
        json={"verification_token": _verify_token(DEPARTMENT_OID)},
    )

    assert response.status_code == 200
    assert api.calls["inserts"][0]["target_oid"] == OTHER_OID


def test_student_cannot_create(api) -> None:
    _act_as(api, STUDENT_OID, ["student"])

    response = api.http.post(f"{PREFIX}/{MATE_OID}/temp-password")

    assert response.status_code == 403
    assert api.calls["inserts"] == []


def test_secretary_cannot_target_admin_account(api) -> None:
    """同班判定之外还有一层目标保护：别人手里的 admin 账号不可被接管。"""
    _act_as(api, SECRETARY_OID, ["secretary"])

    response = api.http.post(f"{PREFIX}/{ADMIN_TARGET_OID}/temp-password")

    assert response.status_code == 403
    assert api.calls["inserts"] == []


def test_department_cannot_target_admin_account(api) -> None:
    """校级但非 admin 的身份同样不能给别人手上的 admin 账号建码。"""
    _act_as(api, DEPARTMENT_OID, ["department"])

    response = api.http.post(
        f"{PREFIX}/{ADMIN_TARGET_OID}/temp-password",
        json={"verification_token": _verify_token(DEPARTMENT_OID)},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "不能为管理员账号创建临时密码"
    assert api.calls["inserts"] == []


def test_admin_can_target_admin_account(api) -> None:
    _act_as(api, ADMIN_OID, ["admin"])

    response = api.http.post(
        f"{PREFIX}/{ADMIN_TARGET_OID}/temp-password",
        json={"verification_token": _verify_token(ADMIN_OID)},
    )

    assert response.status_code == 200


def test_unknown_user_is_404(api) -> None:
    response = api.http.post(f"{PREFIX}/00000000000000000000eeee/temp-password")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /{user_id}/temp-password —— 码与存储
# ---------------------------------------------------------------------------


def test_code_is_eight_digits_and_stored_hashed(api) -> None:
    data = api.http.post(
        f"{PREFIX}/{MATE_OID}/temp-password",
        json={"verification_token": _verify_token(ADMIN_OID)},
    ).json()["data"]

    code = data["code"]
    assert len(code) == 8
    assert code.isdigit() and code.isascii()

    stored = api.calls["inserts"][0]
    # 明文绝不落库：库里只有 bcrypt 哈希，且能反查出刚返回的码。
    assert stored["code_hash"] != code
    assert stored["code_hash"].startswith("$2")
    assert checkpw(code.encode("utf-8"), stored["code_hash"].encode("utf-8"))


def test_expiry_is_thirty_minutes(api) -> None:
    data = api.http.post(
        f"{PREFIX}/{MATE_OID}/temp-password",
        json={"verification_token": _verify_token(ADMIN_OID)},
    ).json()["data"]

    expires_at = api.calls["inserts"][0]["expires_at"]
    assert isinstance(expires_at, datetime)
    delta = expires_at - datetime.now()
    assert timedelta(minutes=29) < delta <= timedelta(minutes=30)
    assert data["expires_at"] == expires_at.isoformat(timespec="seconds")


def test_creator_and_header_are_recorded(api) -> None:
    _act_as(api, SECRETARY_OID, ["secretary"])

    api.http.post(
        f"{PREFIX}/{MATE_OID}/temp-password",
        json={"verification_token": _verify_token(SECRETARY_OID)},
        headers={"Xuehai-Id": "1234567890"},
    )

    stored = api.calls["inserts"][0]
    assert stored["creator_oid"] == SECRETARY_OID
    assert stored["creator_xuehai_id"] == "1234567890"


def test_response_is_not_cacheable_and_logged(api) -> None:
    response = api.http.post(
        f"{PREFIX}/{MATE_OID}/temp-password",
        json={"verification_token": _verify_token(ADMIN_OID)},
    )

    assert response.headers["cache-control"] == "no-store"
    entry = api.log.entries[-1]
    assert entry["type"] == "temp_password_create"
    assert entry["level"] == "info"
    # 明文只能出现在响应体里，日志里一个字都不能有。
    assert response.json()["data"]["code"] not in entry["text"]


# ---------------------------------------------------------------------------
# GET /me/device
# ---------------------------------------------------------------------------


def test_my_device_reports_header_and_binding(api) -> None:
    api.users[STUDENT_OID]["primary_xuehai_id"] = "1234567890"
    _act_as(api, STUDENT_OID, ["student"])

    data = api.http.get(f"{PREFIX}/me/device", headers={"Xuehai-Id": "99"}).json()[
        "data"
    ]

    assert data == {
        "xuehai_id": "99",
        "primary_xuehai_id": "1234567890",
        "device_banned": False,
        "device_ban_reason": None,
        "device_ban_until": None,
    }


def test_my_device_reports_ban_state(api) -> None:
    until = datetime(2026, 11, 1, 0, 0, 0)
    api.status["ban"] = (True, "违规使用", until)
    _act_as(api, STUDENT_OID, ["student"])

    data = api.http.get(
        f"{PREFIX}/me/device", headers={"Xuehai-Id": "1234567890"}
    ).json()["data"]

    assert data["device_banned"] is True
    assert data["device_ban_reason"] == "违规使用"
    assert data["device_ban_until"] == "2026-11-01T00:00:00"


# ---------------------------------------------------------------------------
# PUT /me/primary-device
# ---------------------------------------------------------------------------


def test_first_binding_requires_matching_header(api) -> None:
    _act_as(api, STUDENT_OID, ["student"])

    ok = api.http.put(
        f"{PREFIX}/me/primary-device",
        json={"xuehai_id": "1234567890"},
        headers={"Xuehai-Id": "1234567890"},
    )
    assert ok.status_code == 204
    assert api.calls["set_primary"] == [(STUDENT_OID, "1234567890")]
    assert api.log.entries[-1]["type"] == "device_bind"


def test_first_binding_rejects_other_device(api) -> None:
    _act_as(api, STUDENT_OID, ["student"])

    response = api.http.put(
        f"{PREFIX}/me/primary-device",
        json={"xuehai_id": "1234567890"},
        headers={"Xuehai-Id": "999"},
    )

    assert response.status_code == 400
    assert api.calls["set_primary"] == []


def test_rebinding_only_from_current_primary(api) -> None:
    api.users[STUDENT_OID]["primary_xuehai_id"] = "1234567890"
    _act_as(api, STUDENT_OID, ["student"])

    denied = api.http.put(
        f"{PREFIX}/me/primary-device",
        json={
            "xuehai_id": "555",
            "verification_token": _verify_token(STUDENT_OID),
        },
        headers={"Xuehai-Id": "999"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "只能在主学海设备上更换主学海设备"
    assert api.calls["set_primary"] == []

    ok = api.http.put(
        f"{PREFIX}/me/primary-device",
        json={
            "xuehai_id": "555",
            "verification_token": _verify_token(STUDENT_OID),
        },
        headers={"Xuehai-Id": "1234567890"},
    )
    assert ok.status_code == 204
    assert api.calls["set_primary"] == [(STUDENT_OID, "555")]


@pytest.mark.parametrize("bad", ["", "12ab", "1" * 33, "１２３"])
def test_invalid_device_id_is_400(api, bad) -> None:
    _act_as(api, STUDENT_OID, ["student"])

    # 只发请求体：格式校验先于设备身份比对，非法值不该靠请求头对齐才被拒。
    response = api.http.put(f"{PREFIX}/me/primary-device", json={"xuehai_id": bad})

    assert response.status_code == 400
    assert api.calls["set_primary"] == []


# ---------------------------------------------------------------------------
# DELETE /me/primary-device
# ---------------------------------------------------------------------------


def test_unbind_requires_current_primary(api) -> None:
    api.users[STUDENT_OID]["primary_xuehai_id"] = "1234567890"
    _act_as(api, STUDENT_OID, ["student"])

    denied = api.http.request(
        "DELETE",
        f"{PREFIX}/me/primary-device",
        json={"verification_token": _verify_token(STUDENT_OID)},
        headers={"Xuehai-Id": "999"},
    )
    assert denied.status_code == 403
    assert api.calls["clear_primary"] == []

    ok = api.http.request(
        "DELETE",
        f"{PREFIX}/me/primary-device",
        json={"verification_token": _verify_token(STUDENT_OID)},
        headers={"Xuehai-Id": "1234567890"},
    )
    assert ok.status_code == 204
    assert api.calls["clear_primary"] == [STUDENT_OID]
    assert api.log.entries[-1]["type"] == "device_unbind"


def test_unbind_without_binding_is_409(api) -> None:
    _act_as(api, STUDENT_OID, ["student"])

    response = api.http.request(
        "DELETE",
        f"{PREFIX}/me/primary-device",
        json={"verification_token": _verify_token(STUDENT_OID)},
    )

    assert response.status_code == 409
    assert api.calls["clear_primary"] == []


# ---------------------------------------------------------------------------
# DELETE /{user_id}/primary-device —— 管理员
# ---------------------------------------------------------------------------


def test_admin_clears_any_primary_device(api) -> None:
    api.users[OTHER_OID]["primary_xuehai_id"] = "777"

    response = api.http.request(
        "DELETE",
        f"{PREFIX}/{OTHER_OID}/primary-device",
        json={"verification_token": _verify_token(ADMIN_OID)},
    )

    assert response.status_code == 204
    assert api.calls["clear_primary"] == [OTHER_OID]
    entry = api.log.entries[-1]
    assert entry["type"] == "device_unbind"
    assert "管理员" in entry["text"]


def test_admin_clear_is_idempotent(api) -> None:
    response = api.http.request(
        "DELETE",
        f"{PREFIX}/{OTHER_OID}/primary-device",
        json={"verification_token": _verify_token(ADMIN_OID)},
    )

    assert response.status_code == 204
    assert api.calls["clear_primary"] == []


def test_admin_clear_requires_admin(api) -> None:
    _act_as(api, STUDENT_OID, ["student"])

    response = api.http.delete(f"{PREFIX}/{OTHER_OID}/primary-device")

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /{user_id}/ban-records/reset —— 管理员重置梯度
# ---------------------------------------------------------------------------


def test_admin_resets_all_ban_records(api) -> None:
    response = api.http.post(f"{PREFIX}/{OTHER_OID}/ban-records/reset")

    assert response.status_code == 200
    assert response.json()["data"] == {"deleted": 3}
    assert api.calls["ban_delete"] == [(OTHER_OID, None)]
    entry = api.log.entries[-1]
    assert entry["type"] == "ban_change"
    assert entry["level"] == "warning"


def test_admin_resets_ban_records_for_one_rule(api) -> None:
    response = api.http.post(
        f"{PREFIX}/{OTHER_OID}/ban-records/reset",
        json={"rule_code": "multi_device_login"},
    )

    assert response.status_code == 200
    assert api.calls["ban_delete"] == [(OTHER_OID, "multi_device_login")]


def test_ban_records_reset_requires_admin(api) -> None:
    _act_as(api, DEPARTMENT_OID, ["department"])

    response = api.http.post(f"{PREFIX}/{OTHER_OID}/ban-records/reset")

    assert response.status_code == 403
    assert api.calls["ban_delete"] == []


# ---------------------------------------------------------------------------
# POST /{user_id}/temp-password —— 旧码作废（误封 B10）
# ---------------------------------------------------------------------------


def test_new_code_revokes_previous_open_codes_first(api) -> None:
    """签发新码前先作废旧码：否则旧码正确也会因为不在扫描窗口里被判错。"""
    response = api.http.post(
        f"{PREFIX}/{MATE_OID}/temp-password",
        json={"verification_token": _verify_token(ADMIN_OID)},
    )

    assert response.status_code == 200
    assert api.calls["revoked_open"][0][0] == MATE_OID
    assert api.calls["revoked_open"][0][1] is not None  # 时间戳由路由给
    assert api.calls["ops"] == ["revoke", "insert"]


def test_revocation_target_is_the_code_owner(api) -> None:
    _act_as(api, DEPARTMENT_OID, ["department"])

    api.http.post(
        f"{PREFIX}/{OTHER_OID}/temp-password",
        json={"verification_token": _verify_token(DEPARTMENT_OID)},
    )

    # 作废的是**目标用户**的旧码，不是签发人的。
    assert [oid for oid, _at in api.calls["revoked_open"]] == [OTHER_OID]
