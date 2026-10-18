"""初始密码契约的离线测试。

两部分：

1. ``util.cert.generate_initial_password``（重置路径仍在用）的四条不变量：
   长度 / 字符集 / 刻意不达强度、以及随机性（连续 50 次不重复）。
2. 建号 vs 重置的分叉契约（用户拍板）：
   - ``POST /api/v3/users`` 的**建号**初始密码 == 学号（新生可用学号直接登录），
     落库哈希可用学号 ``check_password`` 通过、且不是其他学号的哈希。
   - 学号 ``pwd_strength < 3``，所以建号账号仍走「弱密码 → 5 分钟临时令牌 →
     强制改密」既有闭环。
   - ``DELETE /api/v3/users/{oid}/password`` 的**重置**仍返回随机一次性密码，
     **绝不**回归学号。

全离线：真跑 ``routers.users_v3_router``（真 ``validate_number`` / 真 bcrypt），
只把 users repo 换成内存假实现、``inject_log`` 换成桩。不连数据库、不发网络。

导入 ``util.cert`` / ``routers.users_v3_router`` 会在 import 时读取仓库根的
``aes_key.txt`` / ``rsa_*.pem``，所以 pytest 必须从仓库根运行（验收命令即如此），
与 ``tests/golden/auth`` 既有引导风格一致。
"""

from __future__ import annotations

import string
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_v3_router
from typings.log import inject_log
from util.cert import check_password, generate_initial_password, pwd_strength
from util.object_id import compulsory_temporary_token, get_current_user

ALPHABET = set(string.ascii_lowercase + string.digits)


def test_length_is_12() -> None:
    assert len(generate_initial_password()) == 12


def test_charset_is_lowercase_letters_and_digits_only() -> None:
    for _ in range(20):
        assert set(generate_initial_password()) <= ALPHABET


def test_strength_stays_below_the_enforcement_threshold() -> None:
    # pwd_strength < 3 正是后端「弱密码 → 只发 5 分钟临时 token → 前端强制改密」
    # 闭环的触发条件；若这里 >= 3，新用户/重置后的账号会绕过强制改密。
    for _ in range(20):
        assert pwd_strength(generate_initial_password()) < 3


def test_fifty_consecutive_draws_are_unique() -> None:
    samples = {generate_initial_password() for _ in range(50)}
    assert len(samples) == 50


# --- 建号 vs 重置的分叉契约 ---------------------------------------------------

PREFIX = "/api/v3/users"
ADMIN = "00000000000000000000000a"
TARGET = "00000000000000000000000b"

# 学号必须过 ``validate_number``：年份取当前「学年窗口」的末尾年，永远合法。
_NOW = datetime.now()
_YEAR = _NOW.year if _NOW.month >= 8 else _NOW.year - 1
NEW_NUMBER = f"{_YEAR}0101"  # 建号用的学号
OTHER_NUMBER = f"{_YEAR}0202"  # 对照组学号
TARGET_NUMBER = f"{_YEAR}0303"  # 被重置账号的学号


class _StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


@pytest.fixture()
def client(monkeypatch):
    state: dict = {"created": [], "reset": []}
    target = {"_id": TARGET, "id": TARGET_NUMBER, "name": "目标用户", "roles": ["student"]}

    async def find_by_business_id(business_id):
        return None

    async def create(business_id, name, roles, password_hash, class_oid):
        state["created"].append(
            {
                "business_id": business_id,
                "name": name,
                "roles": roles,
                "password_hash": password_hash,
                "class_oid": class_oid,
            }
        )
        return TARGET

    async def get_by_oid(oid):
        return target if str(oid) == TARGET else None

    async def set_password_guarded(oid, password_hash, actor_oid):
        state["reset"].append(
            {"oid": oid, "password_hash": password_hash, "actor_oid": actor_oid}
        )
        return 1

    async def display_name(oid):
        return "admin"

    repo = SimpleNamespace(
        find_by_business_id=find_by_business_id,
        create=create,
        get_by_oid=get_by_oid,
        set_password_guarded=set_password_guarded,
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)
    monkeypatch.setattr(users_v3_router, "get_user_display_name", display_name)

    app = FastAPI()
    app.include_router(users_v3_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {"id": ADMIN, "per": ["admin"]}
    app.dependency_overrides[compulsory_temporary_token] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: _StubLog()
    return TestClient(app), state


def test_create_user_initial_password_is_the_student_number(client) -> None:
    http, state = client
    response = http.post(PREFIX, json={"id": NEW_NUMBER, "name": "新生"})
    assert response.status_code == 201

    body = response.json()
    assert body["initial_password"] == NEW_NUMBER

    created = state["created"][0]
    assert created["business_id"] == NEW_NUMBER
    # 落库哈希可用学号验证通过 —— 初始密码就是学号。
    assert check_password(NEW_NUMBER, created["password_hash"]) is True
    # 且不是其他学号的哈希：锁死「== 本人学号」而非任意可枚举值。
    assert check_password(OTHER_NUMBER, created["password_hash"]) is False


def test_created_account_still_triggers_forced_password_change(client) -> None:
    # 学号 8 位纯数字 → pwd_strength == 1 < 3 → 登录走强制改密闭环。
    assert pwd_strength(NEW_NUMBER) < 3

    http, _ = client
    response = http.post(PREFIX, json={"id": NEW_NUMBER, "name": "新生"})
    assert response.status_code == 201
    assert pwd_strength(response.json()["initial_password"]) < 3


def test_reset_password_stays_random_and_is_not_the_student_number(client) -> None:
    http, state = client
    draws = []
    for _ in range(10):
        response = http.delete(f"{PREFIX}/{TARGET}/password")
        assert response.status_code == 200
        draws.append(response.json()["data"]["initial_password"])

    for password in draws:
        # 重置绝不回归学号。
        assert password != TARGET_NUMBER
        assert len(password) == 12
        assert set(password) <= ALPHABET
    # 随机性：连续 10 次不重复。
    assert len(set(draws)) == len(draws)

    # 落库哈希是随机值，学号本身验证不通过。
    for entry, password in zip(state["reset"], draws):
        assert check_password(password, entry["password_hash"]) is True
        assert check_password(TARGET_NUMBER, entry["password_hash"]) is False
