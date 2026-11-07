"""OCR 引擎注册表的 admin 管理面（车道 3b）：CRUD + 脱敏 + 凭证落库口径。

这一层的价值不在「能增删改查」，而在三条**错了不会报错**的约定：

* 响应里永远没有 ``secret_cipher``（连密文都不回），只有 ``key_hint``；
* 入库的是 ``crypto.encrypt_secret`` 的密文，能把凭证原样解回来才算接通；
* 必填字段按 ``kind`` 校验，缺 ``region`` 这种错在录入时就拒——否则要等第一次
  识别（用户已经扣过额度）才炸。

仓储是内存假实现（不连库），加解密走真实 ``crypto``（aes_key.txt 已在仓库根）。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (导入顺序与其它 AI 用例一致)
from db import backends
from db.repos.protocols import DuplicateOcrEngine
from routers import ai_router
from services.ai import crypto, ocr_engines_admin
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"

TENCENT_SECRET = {
    "secret_id": "AKIDexample0000000000000000abcd",
    "secret_key": "tencent-secret-key-0123456789",
    "region": "ap-guangzhou",
}

BAIDU_SECRET = {
    "api_key": "baidu-api-key-0123456789abcdef",
    "secret_key": "baidu-secret-key-fedcba9876543210",
}

GROQ_SECRET = {"api_key": "gsk-groq-0123456789abcdef-never-print"}


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeEngineRepo:
    """``AIOcrEnginesRepo`` 的内存版：排序/唯一键/None=不改 都照协议来。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.seq = 0
        self.calls: list[tuple] = []

    async def list_all(self, *, enabled_only: bool = False) -> list[dict]:
        self.calls.append(("list_all", enabled_only))
        rows = [dict(row) for row in self.rows.values()]
        if enabled_only:
            rows = [row for row in rows if row["enabled"]]
        return sorted(rows, key=lambda row: (row["priority"], row["name"]))

    async def get_by_oid(self, oid: str) -> dict | None:
        self.calls.append(("get_by_oid", oid))
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    async def insert(
        self,
        *,
        name: str,
        kind: str,
        config: dict | None = None,
        secret_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool = True,
        priority: int = 100,
    ) -> str:
        if any(row["name"] == name for row in self.rows.values()):
            raise DuplicateOcrEngine(name)
        self.seq += 1
        oid = f"{self.seq:024x}"
        self.rows[oid] = {
            "_id": oid,
            "name": name,
            "kind": kind,
            "config": dict(config or {}),
            "secret_cipher": secret_cipher,
            "key_hint": key_hint,
            "enabled": bool(enabled),
            "priority": int(priority),
            "created_at": None,
            "updated_at": None,
        }
        return oid

    async def update(self, oid: str, **changes) -> bool:
        row = self.rows.get(oid)
        if row is None:
            return False
        for key, value in changes.items():
            if value is None:  # 协议：None = 不改
                continue
            row[key] = value
        return True

    async def delete(self, oid: str) -> bool:
        return self.rows.pop(oid, None) is not None


@pytest.fixture
def repo(monkeypatch) -> FakeEngineRepo:
    fake = FakeEngineRepo()
    monkeypatch.setattr(
        backends, "get_ai_ocr_engines_repo", lambda session=None: fake
    )
    return fake


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _create(client: TestClient | None = None, **body):
    payload = {
        "name": "primary",
        "kind": "tencent",
        "config": {"language_type": "zh"},
        "secret": dict(TENCENT_SECRET),
        "priority": 10,
    }
    payload.update(body)
    return (client or _client()).post("/api/ai/ocr-engines", json=payload)


# ---------------------------------------------------------------------------
# 读：脱敏
# ---------------------------------------------------------------------------


def test_list_is_closed_to_students(repo):
    assert _client({"id": USER, "per": []}).get("/api/ai/ocr-engines").status_code == 403


@pytest.mark.parametrize("role", ["department", "secretary"])
def test_list_is_readable_by_volunteer_managers_without_ops_fields(role, repo):
    """登记页要渲染引擎下拉：管理者只拿得到公开字段，运维参数一概不给。"""
    assert _create().status_code == 201

    response = _client({"id": USER, "per": [role]}).get("/api/ai/ocr-engines")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "oid": next(iter(repo.rows)),
            "name": "primary",
            "kind": "tencent",
            "enabled": True,
            "priority": 10,
        }
    ]
    # config 是运维参数（endpoint / model），key_hint / has_secret 是内部状态。
    assert "config" not in response.text
    assert "key_hint" not in response.text
    assert "has_secret" not in response.text
    assert TENCENT_SECRET["secret_key"] not in response.text


def test_list_never_returns_secret_cipher(repo):
    assert _create().status_code == 201

    response = _client().get("/api/ai/ocr-engines")

    assert response.status_code == 200
    body = response.json()["data"]
    assert [row["name"] for row in body] == ["primary"]
    assert "secret_cipher" not in response.text
    # 密文本身也不该出现（凭证值一个字都不回）。
    stored = next(iter(repo.rows.values()))["secret_cipher"]
    assert stored not in response.text
    assert TENCENT_SECRET["secret_key"] not in response.text
    assert body[0]["has_secret"] is True
    assert body[0]["config"] == {"language_type": "zh"}
    assert body[0]["priority"] == 10


def test_list_includes_disabled_engines(repo):
    assert _create(priority=10).status_code == 201
    assert _create(name="spare", kind="rapidocr", secret=None, enabled=False).status_code == 201

    body = _client().get("/api/ai/ocr-engines").json()["data"]

    assert [row["name"] for row in body] == ["primary", "spare"]
    assert body[1]["enabled"] is False


# ---------------------------------------------------------------------------
# 写：创建
# ---------------------------------------------------------------------------


def test_create_encrypts_secret_and_hints_identifier(repo):
    response = _create()

    assert response.status_code == 201
    view = response.json()["data"]
    assert view["kind"] == "tencent"
    assert view["has_secret"] is True
    # key_hint 取账号标识（secret_id）的末四位，不是密钥本身的任何部分。
    assert view["key_hint"] == crypto.key_hint(TENCENT_SECRET["secret_id"])

    stored = repo.rows[view["oid"]]
    assert stored["secret_cipher"] != json.dumps(TENCENT_SECRET)
    assert TENCENT_SECRET["secret_key"] not in stored["secret_cipher"]
    # 密文可解回原对象——这比「看起来像 base64」更能证明加解密是接通了的。
    assert json.loads(crypto.decrypt_secret(stored["secret_cipher"])) == TENCENT_SECRET


def test_create_priority_defaults_to_100(repo):
    response = _create(
        name="default-priority", kind="aliyun",
        secret={"access_key_id": "LTAI5tR2example9fZx", "access_key_secret": "aliyun-secret-0123456789"},
        priority=None,
    )
    assert response.status_code == 422  # priority 显式 null 不是 int

    body = {
        "name": "default-priority",
        "kind": "aliyun",
        "secret": {
            "access_key_id": "LTAI5tR2example9fZx",
            "access_key_secret": "aliyun-secret-0123456789",
        },
    }
    created = _client().post("/api/ai/ocr-engines", json=body)

    assert created.status_code == 201
    assert created.json()["data"]["priority"] == 100


def test_create_rapidocr_takes_no_secret(repo):
    ok = _create(name="local", kind="rapidocr", secret=None)

    assert ok.status_code == 201
    view = ok.json()["data"]
    assert view["has_secret"] is False
    assert view["key_hint"] is None
    assert repo.rows[view["oid"]]["secret_cipher"] is None


def test_create_rejects_bad_kind(repo):
    response = _create(kind="tesseract")

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENGINE_BAD_KIND"
    assert repo.rows == {}


def test_create_rejects_missing_required_secret_field(repo):
    partial = {
        "secret_id": "AKIDexample0000000000000000abcd",
        "secret_key": "tencent-secret-key-0123456789",
    }

    response = _create(secret=partial)

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENGINE_INVALID"
    assert repo.rows == {}


def test_create_baidu_requires_both_key_parts(repo):
    # api_key 能标识应用，但换 token 还必须有 secret_key：缺了要在录入时就拒，
    # 不能等第一次识别（用户已经扣过额度）才炸。
    response = _create(
        name="baidu-main",
        kind="baidu",
        secret={"api_key": BAIDU_SECRET["api_key"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENGINE_INVALID"
    # 校验失败不能回显已经提供的密钥值（响应里只有稳定错误码）。
    assert BAIDU_SECRET["api_key"] not in response.text
    assert repo.rows == {}


@pytest.mark.parametrize("secret", [None, {}])
def test_create_groq_requires_api_key(repo, secret):
    response = _create(name="groq-main", kind="groq", secret=secret)

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENGINE_INVALID"
    assert repo.rows == {}


def test_create_baidu_and_groq_encrypt_their_credentials(repo):
    baidu = _create(
        name="baidu-main", kind="baidu", secret=dict(BAIDU_SECRET), priority=20
    )
    groq = _create(
        name="groq-main", kind="groq", secret=dict(GROQ_SECRET), priority=30
    )

    assert (baidu.status_code, groq.status_code) == (201, 201)
    for response, expected, kind in (
        (baidu, BAIDU_SECRET, "baidu"),
        (groq, GROQ_SECRET, "groq"),
    ):
        view = response.json()["data"]
        assert view["kind"] == kind
        assert view["has_secret"] is True
        # hint 取账号标识（api_key）的末四位，不是 secret_key 的任何部分。
        assert view["key_hint"] == crypto.key_hint(expected["api_key"])
        stored = repo.rows[view["oid"]]["secret_cipher"]
        assert stored != json.dumps(expected, sort_keys=True)
        assert json.loads(crypto.decrypt_secret(stored)) == expected


def test_public_view_never_leaks_baidu_or_groq_secret_material(repo):
    baidu = _create(name="baidu-main", kind="baidu", secret=dict(BAIDU_SECRET))
    groq = _create(name="groq-main", kind="groq", secret=dict(GROQ_SECRET))
    assert (baidu.status_code, groq.status_code) == (201, 201)

    # public_view 连密文都不带：只有 has_secret 透露「有没有凭证」。
    view = ocr_engines_admin.public_view(
        repo.rows[baidu.json()["data"]["oid"]]
    )
    assert "secret_cipher" not in view
    assert BAIDU_SECRET["api_key"] not in json.dumps(view)
    assert BAIDU_SECRET["secret_key"] not in json.dumps(view)

    listing = _client().get("/api/ai/ocr-engines")
    assert listing.status_code == 200
    for secret in (BAIDU_SECRET, GROQ_SECRET):
        for value in secret.values():
            assert value not in listing.text
    for row in repo.rows.values():
        assert row["secret_cipher"] not in listing.text


def test_create_rejects_secret_for_rapidocr(repo):
    response = _create(
        name="local", kind="rapidocr", secret={"secret_id": "leaked-into-wrong-place"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENGINE_INVALID"
    # 拒绝时也不能把值回显出去（那等于把「贴错的密钥」写进客户端与日志）。
    assert "leaked-into-wrong-place" not in response.text
    assert repo.rows == {}


def test_create_rejects_bad_name_and_priority(repo):
    assert _create(name="Primary Engine").json()["detail"] == "AI_OCR_ENGINE_INVALID"
    assert _create(name="second", priority=-1).json()["detail"] == "AI_OCR_ENGINE_INVALID"
    assert _create(name="third", priority=1000).json()["detail"] == "AI_OCR_ENGINE_INVALID"
    assert repo.rows == {}


def test_create_duplicate_name_is_409(repo):
    assert _create().status_code == 201

    response = _create(kind="aliyun", secret={"access_key_id": "LTAI5tR2example9fZx", "access_key_secret": "aliyun-secret-0123456789"})

    assert response.status_code == 409
    assert response.json()["detail"] == "AI_OCR_ENGINE_DUPLICATE"
    assert len(repo.rows) == 1


def test_create_requires_admin(repo):
    response = _client({"id": USER, "per": ["department"]}).post(
        "/api/ai/ocr-engines",
        json={"name": "primary", "kind": "rapidocr"},
    )

    assert response.status_code == 403
    assert repo.rows == {}


# ---------------------------------------------------------------------------
# 写：更新 / 删除
# ---------------------------------------------------------------------------


def test_patch_updates_toggle_and_priority(repo):
    oid = _create().json()["data"]["oid"]

    response = _client().patch(
        f"/api/ai/ocr-engines/{oid}", json={"enabled": False, "priority": 5}
    )

    assert response.status_code == 200
    view = response.json()["data"]
    assert view["enabled"] is False
    assert view["priority"] == 5
    # 没传的字段保持原值（exclude_unset 语义）。
    assert view["config"] == {"language_type": "zh"}


def test_patch_rotates_secret(repo):
    oid = _create().json()["data"]["oid"]
    before = repo.rows[oid]["secret_cipher"]

    rotated = {
        "secret_id": "AKIDrotated0000000000000wxyz",
        "secret_key": "rotated-secret-key-9876543210",
        "region": "ap-shanghai",
    }
    response = _client().patch(f"/api/ai/ocr-engines/{oid}", json={"secret": rotated})

    assert response.status_code == 200
    after = repo.rows[oid]["secret_cipher"]
    assert after != before  # 新 nonce + 新明文
    assert json.loads(crypto.decrypt_secret(after)) == rotated
    assert response.json()["data"]["key_hint"] == crypto.key_hint(rotated["secret_id"])
    assert rotated["secret_key"] not in response.text


def test_patch_secret_on_rapidocr_is_rejected(repo):
    oid = _create(name="local", kind="rapidocr", secret=None).json()["data"]["oid"]

    response = _client().patch(
        f"/api/ai/ocr-engines/{oid}", json={"secret": {"secret_id": "nope"}}
    )

    assert response.status_code == 400
    assert repo.rows[oid]["secret_cipher"] is None


def test_patch_unknown_engine_is_404(repo):
    response = _client().patch(
        f"/api/ai/ocr-engines/{'a' * 24}", json={"enabled": False}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "AI_OCR_ENGINE_NOT_FOUND"


def test_patch_requires_admin(repo):
    oid = _create().json()["data"]["oid"]

    response = _client({"id": USER, "per": []}).patch(
        f"/api/ai/ocr-engines/{oid}", json={"enabled": False}
    )

    assert response.status_code == 403
    assert repo.rows[oid]["enabled"] is True


def test_delete_removes_engine(repo):
    oid = _create().json()["data"]["oid"]

    response = _client().delete(f"/api/ai/ocr-engines/{oid}")

    assert response.status_code == 204
    assert repo.rows == {}


def test_delete_unknown_engine_is_404(repo):
    response = _client().delete(f"/api/ai/ocr-engines/{'b' * 24}")

    assert response.status_code == 404
    assert response.json()["detail"] == "AI_OCR_ENGINE_NOT_FOUND"
