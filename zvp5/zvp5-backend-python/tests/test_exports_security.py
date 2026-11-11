"""导出报告下载授权的安全回归。

原漏洞：``GET /api/exports/reports/download`` 只凭 ``?token=``（一段可以被
nginx 日志 / 浏览器历史留存的 base64）就能下载全站 ``export.tar.gz``，既没有
认证，也不校验授权发给谁。现在必须叠加有效 admin JWT，且令牌必须属于当前
登录者；拒绝时响应体不区分失败原因。
"""

import base64
import json
import time
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from routers import exports_router as exports
from util.object_id import get_current_user

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
OTHER_ADMIN = {"id": "000000000000000000000002", "per": ["admin"]}
STUDENT = {"id": "000000000000000000000003", "per": ["student"]}

PATH = "/api/exports/reports/download"


def _grant(granted_to: str, ttl: float = 300) -> str:
    data = {
        "token": uuid.uuid4().hex,
        "granted_to": granted_to,
        "expires_at": datetime.now().timestamp() + ttl,
    }
    deliveries = [data]
    exports.tokens.extend(deliveries)
    return base64.b64encode(json.dumps(data).encode()).decode()


class _StubLog:
    entries: list[tuple[str, str]] = []

    def __init__(self, url: str = "", user: str = "", *args, **kwargs) -> None:
        self.url = url
        self.user = user

    def with_text(self, text: str, type: str = "", level: str = "") -> "_StubLog":
        return self

    async def insert_log(self) -> None:
        self.__class__.entries.append((self.url, self.user))


@pytest.fixture()
def download_env(monkeypatch, tmp_path):
    exports.tokens.clear()
    _StubLog.entries = []

    async def display_name(oid):
        return "管理员"

    monkeypatch.setattr(exports, "ZVMSLog", _StubLog)
    monkeypatch.setattr(exports, "get_user_display_name", display_name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "export.tar.gz").write_bytes(b"archive")
    yield tmp_path
    exports.tokens.clear()


def _client(user=None) -> TestClient:
    app = FastAPI()
    app.include_router(exports.router, prefix="/api/exports")
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_download_without_jwt_is_rejected(download_env) -> None:
    token = _grant(ADMIN["id"])

    response = _client().get(PATH, params={"token": token})

    assert response.status_code == 401
    assert exports.tokens, "未认证的请求不得消费授权"


def test_non_admin_with_valid_grant_is_rejected(download_env) -> None:
    token = _grant(STUDENT["id"])

    response = _client(STUDENT).get(PATH, params={"token": token})

    assert response.status_code == 403
    assert response.json()["detail"] == exports._DOWNLOAD_DENIED
    assert exports.tokens, "非 admin 的请求不得消费授权"


def test_grant_issued_to_someone_else_is_rejected(download_env) -> None:
    token = _grant(OTHER_ADMIN["id"])

    response = _client(ADMIN).get(PATH, params={"token": token})

    assert response.status_code == 403
    assert response.json()["detail"] == exports._DOWNLOAD_DENIED
    assert exports.tokens, "非持有人尝试不得消费授权"


def test_header_token_downloads(download_env) -> None:
    token = _grant(ADMIN["id"])

    response = _client(ADMIN).get(PATH, headers={"X-Download-Token": token})

    assert response.status_code == 200
    assert response.content == b"archive"
    assert exports.tokens == []
    assert _StubLog.entries and "/download" in _StubLog.entries[-1][0]
    assert "token" not in _StubLog.entries[-1][0]


def test_query_token_downloads_for_backwards_compatibility(download_env) -> None:
    token = _grant(ADMIN["id"])

    response = _client(ADMIN).get(PATH, params={"token": token})

    assert response.status_code == 200
    assert exports.tokens == []


def test_grant_is_single_use(download_env) -> None:
    token = _grant(ADMIN["id"])
    client = _client(ADMIN)

    assert client.get(PATH, params={"token": token}).status_code == 200
    assert client.get(PATH, params={"token": token}).status_code == 403


def test_audit_log_does_not_record_query_string(download_env) -> None:
    token = _grant(ADMIN["id"])

    _client(ADMIN).get(PATH, params={"token": token})

    assert _StubLog.entries
    url = _StubLog.entries[-1][0]
    assert token not in url and "?" not in url


@pytest.mark.asyncio
async def test_client_cannot_extend_expired_download_grant(monkeypatch):
    monkeypatch.setattr(exports, "tokens", [{"token": "secret", "expires_at": time.time() - 10, "granted_to": "real-user"}])
    forged = base64.b64encode(json.dumps({"token": "secret", "expires_at": time.time() + 9999, "granted_to": "admin"}).encode()).decode()
    with pytest.raises(HTTPException) as failure:
        await exports.download_reports(
            None, forged, {"id": "real-user", "per": ["admin"]}, None
        )
    assert failure.value.status_code == 403
    assert failure.value.detail == exports._DOWNLOAD_DENIED
    assert exports.tokens == []


@pytest.mark.asyncio
async def test_background_failure_records_failed_status(monkeypatch):
    updates = []

    async def get_by_uuid(value):
        raise RuntimeError("sensitive database error")

    async def update_fields(value, data):
        updates.append(data)

    monkeypatch.setattr(backends, "get_tasks_repo", lambda: SimpleNamespace(get_by_uuid=get_by_uuid, update_fields=update_fields))
    await exports.process_task(str(uuid.uuid4()))
    assert updates[-1]["status"] == "failed"
    assert updates[-1]["task_end"]
    assert "sensitive" not in updates[-1]["errmsg"]


@pytest.mark.asyncio
async def test_end_only_export_preserves_filter(monkeypatch):
    end = datetime(2026, 10, 17)
    observed = []

    async def get_task(value):
        return {"variant": "time", "export_start": None, "export_end": end, "allow_cache": True}

    async def nothing(*args, **kwargs):
        pass

    async def list_all():
        return [{"_id": "user", "id": "123", "name": "Name", "class_id": "class"}]

    async def group_list():
        return [{"_id": "class", "name": "Group"}]

    async def bulk(ids, date_start=None, date_end=None, allow_cache=True, **kwargs):
        observed.append((list(ids), date_start, date_end))
        return {user_id: {"on-campus": 1, "off-campus": 0, "social-practice": 0} for user_id in ids}

    monkeypatch.setattr(backends, "get_tasks_repo", lambda: SimpleNamespace(get_by_uuid=get_task, update_fields=nothing, push_result=nothing))
    monkeypatch.setattr(backends, "get_users_repo", lambda: SimpleNamespace(list_all=list_all))
    monkeypatch.setattr(backends, "get_groups_repo", lambda: SimpleNamespace(list_all=group_list))
    monkeypatch.setattr(exports, "calculate_user_time_bulk", bulk)
    await exports.process_task(str(uuid.uuid4()))
    assert observed == [(["user"], None, end)]
