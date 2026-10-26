"""OCR 接线（车道 3b）：入队选引擎 → 后台识别 → 落库。

这条链路以前只有「noop 占位」，所以真正容易错的是**接线的顺序与归属**：

* 用户额度先 ``peek`` 后 ``consume``，站点额度只在「引擎已选中」之后才扣；
* 站点额度按 ``priority`` 逐个尝试，被额度挡下的引擎不扣费（仓储是原子条件自增），
  所以「第一个打满 → 自动轮换到下一个」是无副作用的；
* 入队选中的引擎必须原样传给后台任务，否则「扣了 A 的站点额度、跑了 B」；
* 手动重识别（attachments 端）走同一套计价：频控 + 用户额度 + 站点额度选引擎，
  只有权限不同（要 manager），没有「免费重识别」这回事。

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
from services.ai import audit, config as ai_config, crypto, ocr_engines, quota
from services.ai import rate_limit
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


class AlwaysOkAdapter(StubAdapter):
    """回退目标：不受 ``StubAdapter.error`` 影响，永远成功。

    用来区分「第一个引擎坏了」与「所有引擎都坏了」——共用同一个桩的话，
    ``StubAdapter.error`` 会让回退也一起失败。
    """

    VERSION = "stub-ok-v1"

    async def recognize(self, image, mime, secret, config):
        StubAdapter.calls.append(
            {"image": image, "mime": mime, "secret": secret, "config": config}
        )
        return {
            "text": "Fallback",
            "lines": [{"text": "Fallback", "confidence": 0.9, "box": None}],
            "engine": "aliyun",
            "raw": {"RequestId": "r-2"},
        }


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_stub():
    StubAdapter.calls = []
    StubAdapter.error = None
    # 频控是进程级滑动窗口：同一 oid 的 OCR 请求数会跨用例累积。
    rate_limit.reset_state()
    yield
    StubAdapter.calls = []
    StubAdapter.error = None
    rate_limit.reset_state()


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
    """四份额度的桩：用户 peek / 用户 consume / 站点 consume / 站点 refund。"""
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
    # 但「这次被拒了」要留一行审计：否则「有人一直在点识别但永远没有引擎」在
    # 后台看不出来。units=0（一分没扣）、status=rejected（我们自己的闸门）。
    assert [(row["status"], row["error_code"], row["units"]) for row in audit_repo.rows] == [
        ("rejected", "AI_OCR_NO_ENGINE", 0)
    ]
    assert audit_repo.rows[0]["attachment_oid"] == ATTACHMENT
    assert audit_repo.rows[0]["purpose"] == "ocr"


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
    # 站点额度全满 = 没接受这次任务：用户额度不扣、不入队；只留一行 rejected 审计。
    assert quotas["consume"].calls == []
    assert files_repo.statuses == []
    assert [(row["status"], row["error_code"]) for row in audit_repo.rows] == [
        ("rejected", "AI_OCR_SITE_QUOTA_EXHAUSTED")
    ]


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


def test_enqueue_consume_race_is_429_and_refunds_site_quota(
    files_repo, engines, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])
    quotas["consume"].result = (False, 9, 9)

    response = _post_ocr()

    assert response.status_code == 429
    assert response.json()["detail"] == "AI_QUOTA_EXCEEDED"
    assert quotas["peek"].calls == [(ADMIN, "ocr", 1)]
    # 站点额度已经扣过一次，必须原样退回去：这次识别根本不会发生。
    assert quotas["site"].calls == [("tencent", 1)]
    assert quotas["refund"].calls == [("tencent",)]
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
        lambda oid, kind=None, name=None, user_oid=None: seen.append((oid, kind, name)),
    )

    response = _post_ocr()

    assert response.status_code == 202
    # 入队选中的引擎与后台任务收到的引擎必须一致，否则扣 A 跑 B。
    assert seen == [(ATTACHMENT, "tencent", "primary")]


# ---------------------------------------------------------------------------
# 入队：体积闸门
# ---------------------------------------------------------------------------


def test_enqueue_rejects_oversized_image(
    files_repo, engines, quotas, audit_repo, monkeypatch
):
    """大图不该进 base64：上游请求体与内存都会被撑爆。"""
    engines([_engine_row("primary", "tencent")])
    monkeypatch.setattr(ai_config, "max_ocr_image_bytes", lambda: 1024)
    files_repo.row["size_bytes"] = 2048

    response = _post_ocr()

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_IMAGE_TOO_LARGE"
    # 拒绝发生在任何计价之前。
    assert quotas["site"].calls == []
    assert quotas["consume"].calls == []
    assert files_repo.statuses == []


def test_enqueue_size_falls_back_to_content_length(
    files_repo, engines, quotas, audit_repo, monkeypatch
):
    """元数据缺 ``size_bytes`` 时按真实内容长度判（异常数据也不能放行）。"""
    engines([_engine_row("primary", "tencent")])
    monkeypatch.setattr(ai_config, "max_ocr_image_bytes", lambda: 4)
    files_repo.row.pop("size_bytes")

    response = _post_ocr()

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_IMAGE_TOO_LARGE"


def test_enqueue_accepts_image_at_the_limit(
    files_repo, engines, quotas, audit_repo, monkeypatch
):
    engines([_engine_row("primary", "tencent")])
    monkeypatch.setattr(ai_config, "max_ocr_image_bytes", lambda: 2048)

    assert _post_ocr().status_code == 202


# ---------------------------------------------------------------------------
# 入队：provider（仅 admin，按引擎 kind 定向）
# ---------------------------------------------------------------------------


def test_enqueue_provider_pins_the_engine_kind(
    files_repo, engines, quotas, audit_repo
):
    """``provider`` 传的是引擎 kind：只在该 kind 里选，priority 让位。"""
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )

    response = _client().post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT, "provider": "aliyun"}
    )

    assert response.status_code == 202
    assert response.json()["data"]["engine"] == "backup"
    # 指定引擎不等于免费：照样扣那一份站点额度，且不碰 tencent 的。
    assert quotas["site"].calls == [("aliyun", 1)]
    assert quotas["consume"].calls == [(ADMIN, "ocr", 1)]


def test_enqueue_provider_is_admin_only(files_repo, engines, quotas, audit_repo):
    engines([_engine_row("primary", "tencent")])

    response = _client({"id": USER, "per": ["department"]}).post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT, "provider": "tencent"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "AI_FORBIDDEN"
    # 门禁在计价之前：被拒的请求一份额度都不碰。
    assert quotas["peek"].calls == []
    assert quotas["site"].calls == []
    # 但拒绝本身要留一行流水（capability 必须是 ocr，不能记到 chat 面上）。
    assert [
        (row["capability"], row["status"], row["error_code"], row["purpose"])
        for row in audit_repo.rows
    ] == [("ocr", "rejected", "AI_FORBIDDEN", "ocr")]


def test_enqueue_rejects_unknown_provider(files_repo, engines, quotas, audit_repo):
    """拼错的 kind 是 400，不是「没有可用引擎」——后者会把运维引去查引擎表。"""
    engines([_engine_row("primary", "tencent")])

    response = _client().post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT, "provider": "tencent_typo"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_OCR_ENGINE_BAD_KIND"
    assert quotas["site"].calls == []


def test_enqueue_provider_without_matching_engine_is_503(
    files_repo, engines, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])

    response = _client().post(
        "/api/ai/ocr", json={"attachment_oid": ATTACHMENT, "provider": "baidu"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "AI_OCR_NO_ENGINE"
    assert quotas["consume"].calls == []
    assert quotas["site"].calls == []


# ---------------------------------------------------------------------------
# 义工登记 OCR：必须带密码复核签发的短时令牌
# ---------------------------------------------------------------------------


def test_volunteer_ocr_requires_a_verification_token(engines):
    """缺 verification_token 是 422：请求体契约先挡住，不进计价。"""
    engines([_engine_row("primary", "tencent")])

    response = _client().post(
        "/api/ai/volunteer-ocr", json={"attachment_oid": ATTACHMENT}
    )

    assert response.status_code == 422


def test_volunteer_ocr_rejects_token_of_another_user(
    monkeypatch, files_repo, engines, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])

    async def other_user(*_args, **_kwargs):
        return {"id": USER, "per": ["admin"], "purpose": "verify"}

    monkeypatch.setattr(ai_router, "_authenticate", other_user)

    response = _client().post(
        "/api/ai/volunteer-ocr",
        json={"attachment_oid": ATTACHMENT, "verification_token": "foreign"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "AI_PASSWORD_VERIFICATION_REQUIRED"
    assert quotas["consume"].calls == []


def test_volunteer_ocr_rejects_a_login_session_token(
    monkeypatch, files_repo, engines, quotas, audit_repo
):
    """短时令牌必须是 purpose=verify：登录态 access token 不能顶替密码复核。"""
    engines([_engine_row("primary", "tencent")])

    async def login_token(*_args, **_kwargs):
        return {"id": ADMIN, "per": ["admin"], "purpose": "login"}

    monkeypatch.setattr(ai_router, "_authenticate", login_token)

    response = _client().post(
        "/api/ai/volunteer-ocr",
        json={"attachment_oid": ATTACHMENT, "verification_token": "session"},
    )

    assert response.status_code == 403
    assert quotas["consume"].calls == []


def test_volunteer_ocr_accepts_verified_password(
    monkeypatch, files_repo, engines, quotas, audit_repo
):
    engines([_engine_row("primary", "tencent")])

    async def verified(*_args, **_kwargs):
        return {"id": ADMIN, "per": ["admin"], "purpose": "verify"}

    monkeypatch.setattr(ai_router, "_authenticate", verified)

    response = _client().post(
        "/api/ai/volunteer-ocr",
        json={"attachment_oid": ATTACHMENT, "verification_token": "proof"},
    )

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "pending"
    # 复核通过后与普通 OCR 同一条计价路径，不存在免费后门。
    assert quotas["consume"].calls == [(ADMIN, "ocr", 1)]


# ---------------------------------------------------------------------------
# 后台识别：process_ocr
# ---------------------------------------------------------------------------


def _install_tencent(adapters) -> None:
    adapters["tencent"] = StubAdapter


def test_process_ocr_success_writes_result(files_repo, engines, adapters):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary", ADMIN)

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

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary", ADMIN)

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


def test_process_ocr_falls_back_when_engine_was_disabled(
    files_repo, engines, adapters, quotas, audit_repo
):
    """入队后引擎被禁用：不锁死，按 priority 换下一个候选跑完。"""
    engines([_engine_row("backup", "aliyun")])
    adapters["aliyun"] = StubAdapter

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary", ADMIN)

    assert files_repo.statuses[-1][1] == "done"
    # 落库的是**实际**成功的引擎，而不是入队时那个已经消失的。
    assert files_repo.ocr_rows[0]["engine"] == "backup"
    # 回退的这一次要扣 backup 那份站点额度（kind=aliyun）。
    assert quotas["site"].calls == [("aliyun", 1)]
    # 第一次尝试的失败也要留流水（model 是入队时的引擎名）。
    assert [(row["model"], row["status"]) for row in audit_repo.rows] == [
        ("primary", "error"),
        ("backup", "success"),
    ]
    assert audit_repo.rows[0]["error_code"] == attachment_ocr.ERROR_ENGINE
    # 逐次流水不重复计费：额度那一笔在入队时已经记过。
    assert [row["units"] for row in audit_repo.rows] == [0, 0]
    # 追踪三键 + 尝试序号：这张图「第几次试了哪个 kind 的引擎」是事实而不是猜测。
    # 第一次尝试的 kind 是入队时要求的那个（tencent），第二次是回退到的 aliyun。
    assert [row["engine_kind"] for row in audit_repo.rows] == ["tencent", "aliyun"]
    assert [row["attachment_oid"] for row in audit_repo.rows] == [ATTACHMENT] * 2
    assert [row["attempt_no"] for row in audit_repo.rows] == [1, 2]
    assert [row["purpose"] for row in audit_repo.rows] == ["ocr", "ocr"]
    # 同一次识别的所有尝试共用同一个 request_id（整条链可聚合）。
    request_ids = {row["request_id"] for row in audit_repo.rows}
    assert len(request_ids) == 1 and request_ids != {None}


def test_process_ocr_fails_over_to_the_next_candidate(
    files_repo, engines, adapters, quotas, audit_repo
):
    """第一个引擎识别失败（上游抖动/额度失效）→ 自动换下一个，落实际成功的引擎。"""
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )
    _install_tencent(adapters)
    adapters["aliyun"] = AlwaysOkAdapter
    StubAdapter.error = ocr_engines.OcrEngineError("tencent upstream is down")

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary", ADMIN)

    assert files_repo.statuses[-1][1] == "done"
    assert files_repo.ocr_rows[0]["engine"] == "backup"
    assert files_repo.ocr_rows[0]["engine_version"] == "stub-ok-v1"
    assert quotas["site"].calls == [("aliyun", 1)]
    assert [row["status"] for row in audit_repo.rows] == ["error", "success"]
    assert audit_repo.rows[0]["error_code"] == attachment_ocr.ERROR_ENGINE


def test_process_ocr_stops_after_three_attempts(
    files_repo, engines, adapters, quotas, audit_repo
):
    """最多试 3 个引擎（入队那个 + 2 次回退），全失败才落 failed。"""
    engines(
        [
            _engine_row("one", "tencent", priority=10),
            _engine_row("two", "aliyun", priority=20),
            _engine_row("three", "baidu", priority=30),
            _engine_row("four", "zhipu", priority=40),
        ]
    )
    for kind in ("tencent", "aliyun", "baidu", "zhipu"):
        adapters[kind] = StubAdapter
    StubAdapter.error = ocr_engines.OcrEngineError("upstream is down")

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "one", ADMIN)

    assert files_repo.statuses[-1] == (ATTACHMENT, "failed", True)
    assert len(StubAdapter.calls) == attachment_ocr.ai_ocr.MAX_ENGINE_ATTEMPTS == 3
    # 第 4 个候选根本没被尝试：也就不该为它扣站点额度。
    assert [call[0] for call in quotas["site"].calls] == ["aliyun", "baidu"]


def test_process_ocr_oversized_content_fails_without_burning_a_fallback(
    files_repo, engines, adapters, quotas, monkeypatch
):
    """真实字节超限是和引擎无关的失败：不回退，白花下一个引擎的额度。"""
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )
    _install_tencent(adapters)
    monkeypatch.setattr(
        attachment_ocr.ai_config, "max_ocr_image_bytes", lambda: 4
    )

    anyio.run(attachment_ocr.process_ocr, ATTACHMENT, "tencent", "primary", ADMIN)

    assert files_repo.statuses[-1] == (ATTACHMENT, "failed", True)
    assert quotas["site"].calls == []
    assert StubAdapter.calls == []
    assert files_repo.ocr_rows == []


# ---------------------------------------------------------------------------
# attachments 端手动重识别（manager）
# ---------------------------------------------------------------------------


def test_attachment_manual_ocr_uses_engine_pool(
    files_repo, engines, adapters, quotas, audit_repo
):
    """attachments 端不再免额度：与 ``/api/ai/ocr`` 同一条计价路径。"""
    engines(
        [
            _engine_row("primary", "tencent", priority=10),
            _engine_row("backup", "aliyun", priority=20),
        ]
    )
    _install_tencent(adapters)

    client = _client(routers=("attachments",))
    response = client.post(f"/api/attachments/{ATTACHMENT}/ocr")

    # attachments 端沿用既有契约：HTTP 200 + body 里的 code 202（表示已入队）。
    assert response.status_code == 200
    assert response.json()["code"] == 202
    # 引擎由入队时选定并按 priority 取第一个，后台任务必须跟着它跑。
    assert response.json()["data"]["engine"] == "primary"
    assert files_repo.ocr_rows and files_repo.ocr_rows[0]["engine"] == "primary"
    # 用户额度与站点额度各扣一次，没有「manager 免费」这回事。
    assert quotas["peek"].calls == [(ADMIN, "ocr", 1)]
    assert quotas["consume"].calls == [(ADMIN, "ocr", 1)]
    assert quotas["site"].calls == [("tencent", 1)]
    # 记账口径与网关一致：引擎凭证是站点级的 → server。
    assert audit_repo.rows and audit_repo.rows[-1]["key_scope"] == "server"


def test_attachment_manual_ocr_site_quota_exhausted_is_503(
    files_repo, engines, adapters, quotas
):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)
    quotas["site"].result = (False, 1000, 1000)

    response = _client(routers=("attachments",)).post(
        f"/api/attachments/{ATTACHMENT}/ocr"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "AI_OCR_SITE_QUOTA_EXHAUSTED"
    # 选不到引擎就不动用户额度：预检只读，真扣只发生在引擎选中之后。
    assert quotas["consume"].calls == []
    assert files_repo.ocr_rows == []


def test_attachment_manual_ocr_processing_is_idempotent(
    files_repo, engines, adapters, quotas
):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)
    files_repo.row["ocr_status"] = "processing"

    response = _client(routers=("attachments",)).post(
        f"/api/attachments/{ATTACHMENT}/ocr"
    )

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert response.json()["data"]["status"] == "processing"
    # 已受理过的不重复扣额度，也不重复入队。
    assert quotas["consume"].calls == []
    assert quotas["site"].calls == []
    assert files_repo.ocr_rows == []


def test_attachment_manual_ocr_rejects_unsupported_mime(
    files_repo, engines, adapters, quotas
):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)
    files_repo.row["mime"] = "application/pdf"

    response = _client(routers=("attachments",)).post(
        f"/api/attachments/{ATTACHMENT}/ocr"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_UNSUPPORTED_MEDIA"
    assert quotas["consume"].calls == []
    assert quotas["site"].calls == []


def test_attachment_manual_ocr_is_manager_only(files_repo, engines, adapters, quotas):
    engines([_engine_row("primary", "tencent")])
    _install_tencent(adapters)

    client = _client({"id": USER, "per": ["student"]}, routers=("attachments",))
    response = client.post(f"/api/attachments/{ATTACHMENT}/ocr")

    assert response.status_code == 403
    assert files_repo.statuses == []
    # 权限在计价之前判定：被拒的请求不碰任何一份额度。
    assert quotas["peek"].calls == []
    assert quotas["site"].calls == []


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
