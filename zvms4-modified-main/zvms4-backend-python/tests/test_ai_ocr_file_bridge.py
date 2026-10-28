"""``POST /api/ai/ocr/file``：文件路径 OCR 桥。

文件预览里的图片没有附件 oid，这条入口把**路径**变成一次同步识别。要钉住的是
「它和附件入口是同一条计费链路，不是第二条免费通道」，以及四道闸的顺序：

* 路径先过 ``file_ops`` 的 jail（越界 403 ``FILES_ROOT_FORBIDDEN``，不读字节）；
* 类型只认 jpeg / png / webp（按扩展名，与 ``ocr.ALLOWED_MIME`` 一一对应）；
* 体积上限复用 ``ai_config.max_ocr_image_bytes()``（不另立一套常量）；
* 门禁是 admin + ``ai.ocr`` 开关，拒绝也落一行审计。

引擎表 / 配额 / 适配器 / 流水全是内存桩：不连库、不出网。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from routers import ai_router
from services.ai import config as ai_config
from services.ai import ocr_engines, quota, rate_limit
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
DEPARTMENT = "000000000000000000000002"


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class FakeEngineRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def list_all(self, *, enabled_only: bool = False) -> list[dict]:
        return [dict(row) for row in self.rows]


class Recorder:
    def __init__(self, result=None, queue: list | None = None) -> None:
        self.calls: list[tuple] = []
        self.result = result
        self.queue = list(queue or [])

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if self.queue:
            return self.queue.pop(0)
        return self.result


class StubAdapter:
    VERSION = "stub-v1"
    calls: list[dict] = []
    error: BaseException | None = None

    async def recognize(self, image, mime, secret, config):
        StubAdapter.calls.append(
            {"image": image, "mime": mime, "secret": secret, "config": config}
        )
        if StubAdapter.error is not None:
            raise StubAdapter.error
        return {"text": "第一行\n第二行", "lines": [], "raw": {}}


def _engine_row(name: str = "primary", kind: str = "tencent") -> dict:
    return {
        "_id": name[0] * 24,
        "name": name,
        "kind": kind,
        "config": {},
        "secret_cipher": None,
        "enabled": True,
        "priority": 10,
        "created_at": None,
        "updated_at": None,
    }


@pytest.fixture(autouse=True)
def _reset_state():
    StubAdapter.calls = []
    StubAdapter.error = None
    rate_limit.reset_state()
    yield
    StubAdapter.calls = []
    StubAdapter.error = None
    rate_limit.reset_state()


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def engines(monkeypatch) -> list[dict]:
    rows = [_engine_row()]
    monkeypatch.setattr(
        backends, "get_ai_ocr_engines_repo", lambda session=None: FakeEngineRepo(rows)
    )
    registry = {"tencent": StubAdapter}
    monkeypatch.setattr(ocr_engines, "ENGINES", registry)
    return rows


@pytest.fixture
def quotas(monkeypatch) -> dict:
    state = {
        "peek": Recorder((True, 0, 100)),
        "consume": Recorder((True, 1, 100)),
        "site": Recorder((True, 1, 100)),
        "refund": Recorder(1),
    }
    monkeypatch.setattr(quota, "peek", state["peek"])
    monkeypatch.setattr(quota, "consume", state["consume"])
    monkeypatch.setattr(quota, "site_consume", state["site"])
    monkeypatch.setattr(quota, "site_refund", state["refund"])
    return state


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _admin() -> TestClient:
    return _client({"id": ADMIN, "per": ["admin"]})


def _image(tmp_path, name: str = "shot.png", size: int = 16):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * size)
    return path


# ---------------------------------------------------------------------------
# 合法路径
# ---------------------------------------------------------------------------


def test_a_valid_image_returns_the_recognized_text(
    tmp_path, engines, quotas, audit_repo
) -> None:
    path = _image(tmp_path)

    response = _admin().post("/api/ai/ocr/file", json={"path": str(path)})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ok",
        "code": 200,
        "data": {"text": "第一行\n第二行"},
    }
    assert StubAdapter.calls[0]["mime"] == "image/png"
    # 计费口径与附件入口完全相同：用户额度一次、站点额度一次。
    assert quotas["consume"].calls == [(ADMIN, "ocr", 1)]
    assert quotas["site"].calls == [("tencent", 1)]
    assert [(row["units"], row["status"]) for row in audit_repo.rows] == [
        (1, "success"),
        (0, "success"),
    ]
    assert audit_repo.rows[0]["endpoint"] == "/api/ai/ocr/file"


@pytest.mark.parametrize("name", ["a.jpg", "b.jpeg", "c.png", "d.webp"])
def test_jpeg_png_webp_are_accepted(tmp_path, engines, quotas, audit_repo, name) -> None:
    response = _admin().post("/api/ai/ocr/file", json={"path": str(_image(tmp_path, name))})
    assert response.status_code == 200, response.text


def test_provider_selects_a_single_engine_kind(
    tmp_path, monkeypatch, quotas, audit_repo
) -> None:
    rows = [_engine_row("primary", "tencent"), _engine_row("backup", "aliyun")]
    monkeypatch.setattr(
        backends, "get_ai_ocr_engines_repo", lambda session=None: FakeEngineRepo(rows)
    )
    registry = {"tencent": StubAdapter, "aliyun": StubAdapter}
    monkeypatch.setattr(ocr_engines, "ENGINES", registry)

    response = _admin().post(
        "/api/ai/ocr/file",
        json={"path": str(_image(tmp_path)), "provider": "aliyun"},
    )

    assert response.status_code == 200, response.text
    assert quotas["site"].calls == [("aliyun", 1)]


def test_all_engines_failing_is_502(tmp_path, engines, quotas, audit_repo) -> None:
    StubAdapter.error = RuntimeError("upstream down")

    response = _admin().post("/api/ai/ocr/file", json={"path": str(_image(tmp_path))})

    assert response.status_code == 502
    assert response.json()["detail"] == "AI_OCR_ENGINE_ERROR"
    # 失败尝试各一行 units=0；入口那条 units=1 仍成立（额度已经花出去了）。
    assert [row["units"] for row in audit_repo.rows] == [1, 0]


# ---------------------------------------------------------------------------
# 四道闸
# ---------------------------------------------------------------------------


def test_outside_the_jail_is_rejected_before_reading_bytes(
    tmp_path, engines, quotas, audit_repo
) -> None:
    response = _admin().post("/api/ai/ocr/file", json={"path": "/etc/passwd"})

    assert response.status_code == 403
    assert response.json()["detail"] == "FILES_ROOT_FORBIDDEN"
    assert StubAdapter.calls == []
    assert quotas["consume"].calls == []


def test_unsupported_extension_is_400(tmp_path, engines, quotas, audit_repo) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    response = _admin().post("/api/ai/ocr/file", json={"path": str(path)})

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_UNSUPPORTED_MEDIA"
    assert StubAdapter.calls == []


def test_oversized_image_is_400(tmp_path, engines, quotas, audit_repo, monkeypatch) -> None:
    monkeypatch.setattr(ai_config, "max_ocr_image_bytes", lambda: 8)
    path = _image(tmp_path)

    response = _admin().post("/api/ai/ocr/file", json={"path": str(path)})

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_IMAGE_TOO_LARGE"
    assert StubAdapter.calls == []


def test_missing_file_is_404(tmp_path, engines, quotas, audit_repo) -> None:
    response = _admin().post(
        "/api/ai/ocr/file", json={"path": str(tmp_path / "ghost.png")}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "AI_OCR_FILE_UNREADABLE"


def test_non_admin_is_forbidden_and_audited(tmp_path, engines, quotas, audit_repo) -> None:
    response = _client({"id": DEPARTMENT, "per": ["department"]}).post(
        "/api/ai/ocr/file", json={"path": str(_image(tmp_path))}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "AI_FORBIDDEN"
    assert StubAdapter.calls == []
    assert quotas["consume"].calls == []
    assert [(row["status"], row["error_code"]) for row in audit_repo.rows] == [
        ("rejected", "AI_FORBIDDEN")
    ]


def test_feature_switch_off_is_403(tmp_path, engines, quotas, audit_repo, monkeypatch) -> None:
    def _denied(user, key):
        raise HTTPException(status_code=403, detail="当前角色无权使用该功能")

    monkeypatch.setattr(ai_router, "ensure_feature", _denied)

    response = _admin().post("/api/ai/ocr/file", json={"path": str(_image(tmp_path))})

    assert response.status_code == 403
    assert StubAdapter.calls == []
    assert quotas["consume"].calls == []


def test_user_quota_exhausted_is_429(tmp_path, engines, quotas, audit_repo) -> None:
    quotas["peek"].result = (False, 7, 7)

    response = _admin().post("/api/ai/ocr/file", json={"path": str(_image(tmp_path))})

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_QUOTA_EXCEEDED"
    assert response.headers["X-AI-Quota-Limit"] == "7"
    assert quotas["consume"].calls == []
