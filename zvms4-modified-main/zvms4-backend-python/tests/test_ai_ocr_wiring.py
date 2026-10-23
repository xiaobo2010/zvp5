"""OCR 接线（车道 3b）：入队选引擎 → 后台识别 → 落库。

这条链路以前只有「noop 占位」，所以真正容易错的是**接线的顺序与归属**：

* 用户额度先 ``peek`` 后 ``consume``，站点额度只在「引擎已选中」之后才扣；
* 站点额度按 ``priority`` 逐个尝试，被额度挡下的引擎不扣费（仓储是原子条件自增），
  所以「第一个打满 → 自动轮换到下一个」是无副作用的；
* 入队选中的引擎必须原样传给后台任务，否则「扣了 A 的站点额度、跑了 B」；
* 手动重识别（attachments 端）是 manager 的低频排障，不扣站点额度。

文件仓储 / 配额 / 引擎表 / 适配器全是内存桩：不连库、不出网、不用真实凭证。
"""

from __future__ import annotations

import json

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from routers import ai_router, attachments_router
from services.ai import audit, crypto, ocr_engines, quota
from typings.log import inject_log
from util import attachment_ocr
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
ATTACHMENT = "f" * 24


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeFilesRepo:
    """只实现 OCR 链路用到的那几个方法。"""

    def __init__(self, row: dict | None = None, content: bytes | None = b"png-bytes"):
        self.row = row
        self.content = content
        self.statuses: list[tuple] = []
        self.events: list[tuple] = []
        self.ocr_rows: list[dict] = []

    async def get_meta(self, oid: str) -> dict | None:
        return dict(self.row) if self.row is not None else None

    async def get_content(self, oid: str) -> bytes | None:
        return self.content

    async def set_ocr(self, oid: str, status: str, *, bump_retry: bool = False) -> None:
        self.statuses.append((oid, status, bump_retry))

    async def add_event(self, *args) -> None:
        self.events.append(args)

    async def add_ocr(self, oid, engine, version, text, confidence, raw) -> str:
        self.ocr_rows.append(
            {
                "file_oid": oid,
                "engine": engine,
                "engine_version": version,
                "text": text,
                "confidence": confidence,
                "raw": raw,
            }
        )
        return "a" * 24


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class FakeEngineRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[bool] = []

    async def list_all(self, *, enabled_only: bool = False) -> list[dict]:
        self.calls.append(enabled_only)
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


def _engine_row(
    name: str,
    kind: str,
    *,
    priority: int = 100,
    secret_cipher: str | None = None,
    config: dict | None = None,
    oid: str | None = None,
) -> dict:
    return {
        "_id": oid or (name[0] * 24),
        "name": name,
        "kind": kind,
        "config": dict(config or {}),
        "secret_cipher": secret_cipher,
        "key_hint": None,
        "enabled": True,
        "priority": priority,
        "created_at": None,
        "updated_at": None,
    }


def _file_row(mime: str = "image/png", status: str | None = None) -> dict:
    return {
        "mime": mime,
        "owner_oid": USER,
        "ocr_status": status,
        "size_bytes": 2048,
    }


class StubAdapter:
    """注册表里的假适配器：记录入参，返回统一形状。"""

    VERSION = "stub-v1"
    calls: list[dict] = []
    error: BaseException | None = None

    def __init__(self) -> None:
        pass

    async def recognize(self, image, mime, secret, config):
        StubAdapter.calls.append(
            {"image": image, "mime": mime, "secret": secret, "config": config}
        )
        if StubAdapter.error is not None:
            raise StubAdapter.error
        return {
            "text": "Hello",
            "lines": [
                {"text": "Hello", "confidence": 0.8, "box": None},
                {"text": "World", "confidence": 0.6, "box": None},
            ],
            "engine": "tencent",
            "raw": {"RequestId": "r-1"},
        }


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_stub():
    StubAdapter.calls = []
    StubAdapter.error = None
    yield
    StubAdapter.calls = []
    StubAdapter.error = None


@pytest.fixture(autouse=True)
def background(monkeypatch) -> Recorder:
    """网关入队路径的后台任务桩：只记参，不跑真实识别。

    ``attachments_router`` 自己 import 了一份 ``process_ocr``，所以这里只影响
    ``/api/ai/ocr``；手动重识别的用例仍然跑真实的 ``process_ocr``。
    """
    recorder = Recorder()
    monkeypatch.setattr(ai_router, "process_ocr", recorder)
    return recorder


@pytest.fixture
def files_repo(monkeypatch) -> FakeFilesRepo:
    fake = FakeFilesRepo(_file_row())
    monkeypatch.setattr(backends, "get_files_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def engines(monkeypatch):
    """装上引擎表：``engines(rows)`` 返回同一个 FakeEngineRepo 方便断言。"""
    state = {"repo": FakeEngineRepo([])}

    def install(rows: list[dict]) -> FakeEngineRepo:
        state["repo"] = FakeEngineRepo(rows)
        return state["repo"]

    monkeypatch.setattr(
        backends,
        "get_ai_ocr_engines_repo",
        lambda session=None: state["repo"],
    )
    return install


@pytest.fixture
def quotas(monkeypatch):
    """三份额度的桩：用户 peek / 用户 consume / 站点 consume。"""
    state = {
        "peek": Recorder((True, 0, 100)),
        "consume": Recorder((True, 1, 100)),
        "site": Recorder((True, 1, 100)),
    }
    monkeypatch.setattr(quota, "peek", state["peek"])
    monkeypatch.setattr(quota, "consume", state["consume"])
    monkeypatch.setattr(quota, "site_consume", state["site"])
    return state


@pytest.fixture
def adapters(monkeypatch):
    """把注册表换成桩：``adapters(StubAdapter)`` 装上并返回同一 dict。"""
    registry = dict(ocr_engines.ENGINES)
    monkeypatch.setattr(ocr_engines, "ENGINES", registry)
    return registry


def _client(user: dict | None = None, routers=("ai",)) -> TestClient:
    app = FastAPI()
    if "ai" in routers:
        app.include_router(ai_router.router, prefix="/api/ai")
    if "attachments" in routers:
        app.include_router(attachments_router.router, prefix="/api/attachments")
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _post_ocr(client: TestClient | None = None):
    return (client or _client()).post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT}
    )


# ---------------------------------------------------------------------------
# 入队：引擎选择
# ---------------------------------------------------------------------------


def test_enqueue_without_engine_is_503(files_repo, engines, quotas, audit_repo):
    engines([])

    response = _post_ocr()

    assert response.status_code == 503
    assert response.json()["detail"] == "AI_OCR_NO_ENGINE"
    # 没引擎 = 没有可执行的活：用户额度一分不扣，附件也不进队列。
    assert quotas["consume"].calls == []
    assert quotas["site"].calls == []
    assert files_repo.statuses == []
    assert audit_repo.rows == []


def test_enqueue_picks_first_engine_by_priority(
    files_repo, engines, quotas, audit_repo
):
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )

    response = _post_ocr()

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["engine"] == "primary"
    assert data["status"] == "pending"
    # engine_kind 是路由层与后台任务之间的内部字段，不进 HTTP 响应。
    assert "engine_kind" not in data
    assert quotas["site"].calls == [("tencent", 1)]
    assert quotas["consume"].calls == [(ADMIN, "ocr", 1)]


def test_enqueue_rotates_when_site_quota_exhausted(
    files_repo, engines, quotas, audit_repo
):
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )
    quotas["site"].queue = [(False, 100, 100), (True, 1, 100)]

    response = _post_ocr()

    assert response.status_code == 202
    assert response.json()["data"]["engine"] == "backup"
    assert [call[0] for call in quotas["site"].calls] == ["tencent", "aliyun"]
    # 用户额度只扣一次：轮换发生在站点额度层，不该碰用户额度。
    assert quotas["consume"].calls == [(ADMIN, "ocr", 1)]


def test_enqueue_all_site_quota_exhausted_is_503(
    files_repo, engines, quotas, audit_repo
):
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )
    quotas["site"].queue = [(False, 100, 100), (False, 50, 50)]

    response = _post_ocr()

    assert response.status_code == 503
    assert response.json()["detail"] == "AI_OCR_SITE_QUOTA_EXHAUSTED"
    # 站点额度全满 = 没接受这次任务：用户额度不扣、不入队、不写审计。
    assert quotas["consume"].calls == []
    assert files_repo.statuses == []
    assert audit_repo.rows == []


def test_enqueue_user_quota_is_prechecked_before_site_quota(
    files_repo, engines, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])
    quotas["peek"].result = (False, 7, 7)

    response = _post_ocr()

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_QUOTA_EXCEEDED"
    assert response.headers["X-AI-Quota-Used"] == "7"
    assert response.headers["X-AI-Quota-Limit"] == "7"
    # 预检被拒就没有「真扣」这一步，也没有占站点额度。
    assert quotas["consume"].calls == []
    assert quotas["site"].calls == []
    assert files_repo.statuses == []


def test_enqueue_consume_race_is_429(files_repo, engines, quotas, audit_repo):
    engines([_engine_row("primary", "tencent")])
    quotas["consume"].result = (False, 9, 9)

    response = _post_ocr()

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_QUOTA_EXCEEDED"
    assert quotas["peek"].calls == [(ADMIN, "ocr", 1)]
    assert files_repo.statuses == []


def test_enqueue_audit_records_engine_name_and_server_scope(
    files_repo, engines, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])

    assert _post_ocr().status_code == 202

    assert [row["provider"] for row in audit_repo.rows] == ["primary"]
    # 引擎凭证是站点级的（全站生效），审计必须记 server 而不是 personal。
    assert [row["key_scope"] for row in audit_repo.rows] == ["server"]
    assert [row["capability"] for row in audit_repo.rows] == ["ocr"]
    assert [row["units"] for row in audit_repo.rows] == [1]
    assert audit_repo.rows[0]["endpoint"] == audit.ENDPOINT_OCR


def test_enqueue_hides_engine_kind_from_response_but_keeps_task_aligned(
    files_repo, engines, quotas, audit_repo, monkeypatch
):
    engines([_engine_row("primary", "tencent")])
    seen: list[tuple] = []
    monkeypatch.setattr(
        ai_router,
        "process_ocr",
        lambda oid, kind=None, name=None: seen.append((oid, kind, name)),
    )

    response = _post_ocr()

    assert response.status_code == 202
    # 入队选中的引擎与后台任务收到的引擎必须一致，否则扣 A 跑 B。
    assert seen == [(ATTACHMENT, "tencent", "primary")]


# ---------------------------------------------------------------------------
# 后台识别：process_ocr
# ---------------------------------------------------------------------------


def _install_tencent(adapters) -> None:
    adapters["tencent"] = StubAdapter


def test_process_ocr_success_writes_result(files_repo, engines, adapters):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary")

    assert [status for _oid, status, _bump in files_repo.statuses] == [
        "processing",
        "done",
    ]
    row = files_repo.ocr_rows[0]
    assert row["engine"] == "primary"
    assert row["engine_version"] == "stub-v1"
    assert row["text"] == "Hello"
    # 多行时取平均：0.8 与 0.6 -> 0.7。
    assert row["confidence"] == pytest.approx(0.7)
    assert row["raw"] == {"RequestId": "r-1"}
    # mime 来自附件元数据，secret 为空（这行没配凭证）。
    assert StubAdapter.calls[0]["mime"] == "image/png"
    assert StubAdapter.calls[0]["secret"] == {}
    assert files_repo.events[-1][2] == "ocr_done"


def test_process_ocr_failure_lands_failed_and_swallows(
    files_repo, engines, adapters, caplog
):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)
    StubAdapter.error = ocr_engines.OcrEngineError("tencent engine requires secret field 'region'")

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary")

    assert files_repo.statuses[-1] == (ATTACHMENT, "failed", True)
    assert files_repo.ocr_rows == []
    # 后台任务不把异常抛回调用方（FastAPI 的后台异常只会打成噪音日志）。
    assert "requires secret field" in caplog.text


def test_process_ocr_decrypts_secret_before_calling_adapter(
    files_repo, engines, adapters, monkeypatch
):
    cipher = crypto.encrypt_secret(
        json.dumps({"secret_id": "id-1234", "secret_key": "sk-xyz", "region": "ap-guangzhou"})
    )
    engines(
        [
            _engine_row("primary", "tencent", secret_cipher=cipher),
        ]
    )
    _install_tencent(adapters)

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary")

    assert StubAdapter.calls[0]["secret"] == {
        "secret_id": "id-1234",
        "secret_key": "sk-xyz",
        "region": "ap-guangzhou",
    }
    # 明文凭证不许出现在写库结果里。
    assert "sk-xyz" not in json.dumps(files_repo.ocr_rows, default=str)


def test_process_ocr_manual_path_uses_first_enabled_and_skips_site_quota(
    files_repo, engines, adapters, quotas
):
    repo = engines(
        [
            _engine_row("backup", "aliyun"),
            _engine_row("primary", "tencent", priority=20),
        ]
    )
    adapters["aliyun"] = StubAdapter

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT)

    # 只查 enabled 行；手动重识别是 manager 的低频排障，不扣站点额度。
    assert repo.calls == [True]
    assert quotas["site"].calls == []
    assert files_repo.ocr_rows[0]["engine"] == "backup"


def test_process_ocr_fails_when_engine_was_disabled(files_repo, engines, adapters, caplog):
    engines([_engine_row("backup", "aliyun")])
    adapters["tencent"] = StubAdapter

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary")

    # 引擎入队后被禁用：如实失败（可重试），不悄悄换一个引擎去花另一份额度。
    assert files_repo.statuses[-1][1] == "failed"
    assert "no longer enabled" in caplog.text


# ---------------------------------------------------------------------------
# attachments 端手动重识别（manager）
# ---------------------------------------------------------------------------


def test_attachment_manual_ocr_does_not_touch_site_quota(
    files_repo, engines, adapters, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)

    client = _client(routers=("attachments",))
    response = client.post(f"/api/attachments/{ATTACHMENT}/ocr")

    # attachments 端沿用既有契约：HTTP 200 + body 里的 code 202（表示已入队）。
    assert response.status_code == 200
    assert response.json()["code"] == 202
    # TestClient 会同步跑完 background task：识别结果应当已经落库。
    assert files_repo.ocr_rows and files_repo.ocr_rows[0]["engine"] == "primary"
    assert quotas["site"].calls == []
    assert quotas["consume"].calls == []
    assert quotas["peek"].calls == []
    assert audit_repo.rows == []


def test_attachment_manual_ocr_is_manager_only(files_repo, engines, adapters, quotas):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)

    client = _client({"id": USER, "per": ["student"]}, routers=("attachments",))
    response = client.post(f"/api/attachments/{ATTACHMENT}/ocr")

    assert response.status_code == 403
    assert files_repo.statuses == []


# ---------------------------------------------------------------------------
# 注册表：VERSION 常量
# ---------------------------------------------------------------------------


def test_adapters_expose_version_constants():
    assert ocr_engines.TencentOcrEngine.VERSION.startswith("tencent-")
    assert ocr_engines.AliyunOcrEngine.VERSION.startswith("aliyun-")
    assert ocr_engines.RapidOcrEngine.VERSION.startswith("rapidocr-")
    # 版本号用于 file_ocr.engine_version，必须非空且各 kind 互不相同。
    versions = {
        ocr_engines.TencentOcrEngine.VERSION,
        ocr_engines.AliyunOcrEngine.VERSION,
        ocr_engines.RapidOcrEngine.VERSION,
    }
    assert len(versions) == 3
