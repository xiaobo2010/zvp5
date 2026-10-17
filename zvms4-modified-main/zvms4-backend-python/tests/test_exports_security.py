import base64
import json
import time
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from db import backends
from routers import exports_router as exports


@pytest.mark.asyncio
async def test_client_cannot_extend_expired_download_grant(monkeypatch):
    monkeypatch.setattr(exports, "tokens", [{"token": "secret", "expires_at": time.time() - 10, "granted_to": "real-user"}])
    forged = base64.b64encode(json.dumps({"token": "secret", "expires_at": time.time() + 9999, "granted_to": "admin"}).encode()).decode()
    with pytest.raises(HTTPException) as failure:
        await exports.download_reports(None, forged)
    assert failure.value.status_code == 403
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

    async def group(value):
        return {"name": "Group"}

    async def calculate(*args, **kwargs):
        observed.append(args)
        return {"on-campus": 1, "off-campus": 0, "social-practice": 0}

    monkeypatch.setattr(backends, "get_tasks_repo", lambda: SimpleNamespace(get_by_uuid=get_task, update_fields=nothing, push_result=nothing))
    monkeypatch.setattr(backends, "get_users_repo", lambda: SimpleNamespace(list_all=list_all))
    monkeypatch.setattr(backends, "get_groups_repo", lambda: SimpleNamespace(get=group))
    monkeypatch.setattr(exports, "calculate_user_time", calculate)
    await exports.process_task(str(uuid.uuid4()))
    assert observed == [("user", None, end)]
