"""班级内建号（``POST /api/v1/users``）的学号轻校验契约。

背景：学号格式限制从建号口移除——班级内建号的编号规则由各班级自定，不再要求
8 位纯数字 + 年份窗口 + 班号/座号范围。**只剩三类硬约束**（会真的炸后端的）：

1. strip 后非空（空登录名无法登录 / 无法被 ``find_by_business_id`` 命中）；
2. 长度不超过 ``users.business_id`` 的列宽 —— ``VARCHAR2(32 CHAR)``；
3. 不含空白与控制字符（登录表单与审计日志的脏数据来源）。

唯一性（预检 409 + ``uq_users_business_id`` 兜底）、班级存在性、角色白名单全部
保持不变，这里一并回归。改学号端点现在也走同一套 ``_normalize_student_id``
（不再做 8 位数字/年份窗口/班号座号格式闸），相关回归见
``tests/test_v4242_user_admin.py``。

全离线：真跑 ``routers.users_router``（真 bcrypt），只把 users repo 换成内存
假实现、``inject_log`` 换成桩。需从仓库根运行 pytest（import 时读密钥文件）。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_router
from typings.log import inject_log
from util.cert import check_password, jwt_decode, pwd_strength, rsa_encrypt
from util.object_id import get_current_user

PREFIX = "/api/v1/users"
ADMIN = "00000000000000000000000a"
NEW_OID = "00000000000000000000000b"
EXISTING_NUMBER = "20240101"
MAX_LEN = 32


class _StubLog:
    def __init__(self) -> None:
        self.calls = []

    def with_text(self, text, type="", level=""):
        self.calls.append({"text": text, "type": type, "level": level})
        return self

    async def insert_log(self):
        return None


def _make_client(monkeypatch, existing_numbers=(), create_raises=None):
    created = []
    log = _StubLog()

    async def find_by_business_id(business_id):
        if business_id in existing_numbers:
            return {"_id": "0000000000000000000000ff", "id": business_id}
        return None

    async def create(business_id, name, roles, password_hash, class_oid):
        if create_raises is not None:
            raise create_raises
        created.append(
            {
                "business_id": business_id,
                "name": name,
                "roles": roles,
                "password_hash": password_hash,
                "class_oid": class_oid,
            }
        )
        return NEW_OID

    async def display_name(oid):
        return "admin"

    repo = SimpleNamespace(find_by_business_id=find_by_business_id, create=create)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)
    monkeypatch.setattr(users_router, "get_user_display_name", display_name)

    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {"id": ADMIN, "per": ["admin"]}
    app.dependency_overrides[inject_log] = lambda: log
    return TestClient(app), created, log


def _post(http, body):
    return http.post(PREFIX, json=body)


# ---------------------------------------------------------------------------
# 1. 放宽后：非 8 位学号必须建得成
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "student_id",
    [
        "23001",  # 5 位纯数字
        "7",  # 1 位
        "A2301",  # 带字母
        "2301-05",  # 带符号（班级自定义分隔）
        "2024级_01班-07",  # 中文 + 符号
        "x" * MAX_LEN,  # 列宽边界：32 字符
    ],
)
def test_non_eight_digit_number_can_be_created(monkeypatch, student_id) -> None:
    http, created, log = _make_client(monkeypatch)

    response = _post(http, {"id": student_id, "name": "新生"})

    assert response.status_code == 201
    assert response.json() == {"id": NEW_OID, "initial_password": student_id}
    assert created[0]["business_id"] == student_id
    # 初始密码 == 学号 的既有契约不变：哈希可用学号验证通过。
    assert check_password(student_id, created[0]["password_hash"]) is True
    assert log.calls[0]["type"] == "user_create"
    assert student_id in log.calls[0]["text"]


def test_old_school_year_window_no_longer_blocks_creation(monkeypatch) -> None:
    # 旧 validate_number 会因年份不在窗口内拒绝（如 2019 年的学号）；
    # 建号口放宽后这类学号照常放行。
    http, created, _ = _make_client(monkeypatch)

    response = _post(http, {"id": "20190101", "name": "留级生"})

    assert response.status_code == 201
    assert created[0]["business_id"] == "20190101"


def test_surrounding_spaces_are_trimmed_before_persisting(monkeypatch) -> None:
    http, created, _ = _make_client(monkeypatch)

    response = _post(http, {"id": "  A2301  ", "name": "新生"})

    assert response.status_code == 201
    # 落库与初始密码都用 strip 后的值：否则登录表单 trimmed 输入永远对不上。
    assert created[0]["business_id"] == "A2301"
    assert response.json()["initial_password"] == "A2301"


# ---------------------------------------------------------------------------
# 2. 轻校验仍然要拦的三类
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("student_id", ["", "   ", "\t\n"])
def test_blank_number_is_400(monkeypatch, student_id) -> None:
    http, created, log = _make_client(monkeypatch)

    response = _post(http, {"id": student_id, "name": "新生"})

    assert response.status_code == 400
    assert response.json()["detail"] == "学号不能为空"
    assert created == []
    assert log.calls == []


def test_too_long_number_is_400_at_column_width(monkeypatch) -> None:
    http, created, log = _make_client(monkeypatch)

    response = _post(http, {"id": "x" * (MAX_LEN + 1), "name": "新生"})

    assert response.status_code == 400
    assert response.json()["detail"] == f"学号长度不能超过 {MAX_LEN} 个字符"
    assert created == []
    assert log.calls == []


@pytest.mark.parametrize("student_id", ["23 01", "23\u00a001", "2301\x07", "23\x7f01", "23\x8501"])
def test_whitespace_or_control_char_is_400(monkeypatch, student_id) -> None:
    http, created, log = _make_client(monkeypatch)

    response = _post(http, {"id": student_id, "name": "新生"})

    assert response.status_code == 400
    assert response.json()["detail"] == "学号不能包含空白或控制字符"
    assert created == []
    assert log.calls == []


# ---------------------------------------------------------------------------
# 3. 放宽不能把唯一性 / 权限闸一起放掉
# ---------------------------------------------------------------------------


def test_duplicate_number_is_still_409(monkeypatch) -> None:
    http, created, _ = _make_client(monkeypatch, existing_numbers={EXISTING_NUMBER})

    response = _post(http, {"id": EXISTING_NUMBER, "name": "新生"})

    assert response.status_code == 409
    assert response.json()["detail"] == "学号已存在"
    assert created == []


def test_unique_constraint_race_is_still_409(monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    http, _, _ = _make_client(
        monkeypatch, create_raises=IntegrityError("INSERT", {}, Exception("unique"))
    )

    response = _post(http, {"id": "A2301", "name": "新生"})

    assert response.status_code == 409
    assert response.json()["detail"] == "学号已存在"


def test_admin_role_still_rejected_on_create(monkeypatch) -> None:
    http, created, _ = _make_client(monkeypatch)

    response = _post(http, {"id": "A2301", "name": "新生", "roles": ["admin"]})

    assert response.status_code == 403
    assert created == []


def test_non_admin_still_forbidden(monkeypatch) -> None:
    http, created, _ = _make_client(monkeypatch)
    # 覆盖依赖：普通学生不能建号。
    from util.object_id import get_current_user as dep

    http.app.dependency_overrides[dep] = lambda: {"id": ADMIN, "per": ["student"]}

    response = _post(http, {"id": "A2301", "name": "新生"})

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"
    assert created == []


# ---------------------------------------------------------------------------
# 4. pwd_strength 边界：初始密码=学号 仍要走强制改密闭环
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("student_id", ["23001", "A2301", "2301-05", "2024级_01班-07"])
def test_initial_password_stays_weak_for_common_custom_numbers(
    monkeypatch, student_id
) -> None:
    # 短于 8 位或只含单一/两字符类 → pwd_strength < 3 → 「弱密码 → 5 分钟临时
    # 令牌 → 强制改密」闭环照旧触发。
    http, _, _ = _make_client(monkeypatch)

    response = _post(http, {"id": student_id, "name": "新生"})

    assert response.status_code == 201
    assert pwd_strength(response.json()["initial_password"]) < 3


def test_known_edge_strong_looking_number_skips_forced_change(monkeypatch) -> None:
    """学号形状本身的边界（引脚：pwd_strength 只回答形状，不回答「是不是学号」）。

    ``pwd_strength`` 是「8-14 位、≥3 个字符类」的形状判定，与「是不是学号」无关。
    若某个班级把学号定成 ``Abc12345`` 这类 8 位以上、大小写+数字齐活的值，单看
    强度它是**强**密码。强制改密由登录侧的另一条前置判定兜住（密码 == 学号 →
    无条件临时令牌），见下面 ``test_*_login_*`` 的登录链路用例。
    """
    http, _, _ = _make_client(monkeypatch)
    strong_number = "Abc12345"

    assert pwd_strength(strong_number) >= 3

    response = _post(http, {"id": strong_number, "name": "新生"})

    assert response.status_code == 201
    assert pwd_strength(response.json()["initial_password"]) >= 3


# ---------------------------------------------------------------------------
# 5. 登录侧：密码 == 学号 → 无条件强制改密（不看 pwd_strength）
# ---------------------------------------------------------------------------

LOGIN_PREFIX = "/api/users"


class _SilentLog:
    """登录链路的审计桩：只吞掉写入，不连库。"""

    def __init__(self, *args, **kwargs):
        pass

    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class _NoRuleRepo:
    async def get_by_trigger(self, trigger):
        return None

    async def get_by_code(self, code):
        return None


class _LoginRepo:
    def __init__(self, doc):
        self.doc = doc

    async def get_by_oid(self, oid):
        return self.doc if str(oid) == self.doc["_id"] else None

    async def find_by_business_id(self, business_id):
        return self.doc if business_id == self.doc["id"] else None


def _credential(password: str) -> str:
    return rsa_encrypt(
        json.dumps({"password": password, "time": datetime.now().timestamp()})
    )


def _login(monkeypatch, doc, password):
    users_router._login_failure_logged.clear()
    monkeypatch.setattr(users_router, "ZVMSLog", _SilentLog)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _LoginRepo(doc))
    monkeypatch.setattr(backends, "get_ban_rules_repo", lambda *a, **k: _NoRuleRepo())
    app = FastAPI()
    app.include_router(users_router.router, prefix=LOGIN_PREFIX)
    return TestClient(app).post(
        f"{LOGIN_PREFIX}/auth",
        json={"id": doc["_id"], "mode": "long", "credential": _credential(password)},
    )


def test_multi_class_number_login_still_forces_password_change(monkeypatch) -> None:
    """建号 ``Abc12345`` → 用学号登录 → 仍是 5 分钟临时令牌 + 必须改密。

    ``Abc12345`` 单看 ``pwd_strength`` 是强密码（>=3）；没有「密码 == 学号」的
    前置判定时，这里会拿到 10 天长效 access_token，跳过强制改密。
    """
    student_id = "Abc12345"
    assert pwd_strength(student_id) >= 3

    http, created, _ = _make_client(monkeypatch)
    created_response = _post(
        http, {"id": student_id, "name": "新生", "roles": ["secretary"]}
    )
    assert created_response.status_code == 201

    doc = {
        "_id": NEW_OID,
        "id": student_id,
        "name": "新生",
        "password": created[0]["password_hash"],
        "roles": ["secretary"],
    }
    response = _login(monkeypatch, doc, student_id)

    assert response.status_code == 200
    body = response.json()
    assert body["weak_password"] is True
    payload = jwt_decode(body["token"])
    assert payload["scope"] == "temporary_token"
    assert payload["purpose"] == "password_change"


def test_eight_digit_number_login_keeps_old_behaviour(monkeypatch) -> None:
    # 旧语义不回归：8 位纯数字学号作初始密码仍然只发临时令牌。
    student_id = "20240101"

    http, created, _ = _make_client(monkeypatch)
    assert _post(http, {"id": student_id, "name": "新生", "roles": ["secretary"]}).status_code == 201

    doc = {
        "_id": NEW_OID,
        "id": student_id,
        "name": "新生",
        "password": created[0]["password_hash"],
        "roles": ["secretary"],
    }
    response = _login(monkeypatch, doc, student_id)

    assert response.status_code == 200
    assert response.json()["weak_password"] is True
    payload = jwt_decode(response.json()["token"])
    assert payload["scope"] == "temporary_token"


def test_changed_password_login_still_gets_access_token(monkeypatch) -> None:
    # 已改过密码（密码 != 学号、且强度达标）→ 照常长效 token，不误伤正常用户。
    from util.cert import hash_password

    student_id = "Abc12345"
    strong_password = "StrongPass1!"
    assert strong_password != student_id
    assert pwd_strength(strong_password) >= 3

    doc = {
        "_id": NEW_OID,
        "id": student_id,
        "name": "新生",
        "password": hash_password(strong_password).decode("utf-8"),
        "roles": ["secretary"],
    }
    response = _login(monkeypatch, doc, strong_password)

    assert response.status_code == 200
    assert response.json()["weak_password"] is False
    payload = jwt_decode(response.json()["token"])
    assert payload["scope"] == "access_token"
