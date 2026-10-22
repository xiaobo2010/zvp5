import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from db import backends
from routers import activities_v3_router as activities, imports_router as imports

ACTIVITY = "000000000000000000000010"
USER = "000000000000000000000001"
OTHER = "000000000000000000000002"


class Session:
    def __init__(self, store):
        self.store, self.pending, self.locked = store, None, False

    @property
    def data(self):
        if self.pending is None:
            self.pending = deepcopy(self.store.data)
        return self.pending

    @asynccontextmanager
    async def begin(self):
        try:
            yield
        except BaseException:
            raise
        else:
            if self.pending is not None:
                self.store.data = self.pending
        finally:
            if self.locked:
                self.store.lock.release()


class Repos:
    def __init__(self, session, kind):
        self.session, self.kind = session, kind

    async def get_for_update(self, oid):
        await self.session.store.lock.acquire()
        self.session.locked = True
        return {"_id": oid, "type": "hybrid", "name": "Activity"}

    async def get_by_oid(self, oid):
        return {"_id": oid}

    async def get_many_by_oids(self, oids):
        # activities 的批量用户读必须在 activity 行锁之后；imports 的存在性校验
        # 在事务内、activity 行创建之前，同样走这个批量接口。
        assert self.session.locked or self.session.store.data["activities"] == 0
        await asyncio.sleep(0)
        self.session.store.bulk_calls.append(list(oids))
        return {
            oid: {"_id": oid}
            for oid in dict.fromkeys(oids)
            if oid not in self.session.store.unknown
        }

    async def get_names(self, oids):
        # 审计文案的人名解析：同一事务内批量查，不额外开连接。
        await asyncio.sleep(0)
        return {oid: f"用户{oid[-2:]}" for oid in dict.fromkeys(oids)}

    async def get_by_triple(self, activity, oid, mode):
        assert self.session.locked
        await asyncio.sleep(0)
        value = self.session.data["members"].get((oid, mode))
        return {"_id": (oid, mode), "duration": value} if value is not None else None

    async def list_by_activity_and_members(self, activity, member_oids):
        # 批量版 get_by_triple：同样在事务内读，供 handler 内存判定 upsert。
        assert self.session.locked
        await asyncio.sleep(0)
        rows = []
        for oid in dict.fromkeys(member_oids):
            for mode in ("on-campus", "off-campus", "social-practice"):
                value = self.session.data["members"].get((oid, mode))
                if value is not None:
                    rows.append(
                        {
                            "_id": (oid, mode),
                            "member": oid,
                            "mode": mode,
                            "duration": value,
                        }
                    )
        return rows

    async def add(self, activity, oid, mode, duration, status):
        if oid == self.session.store.fail_on:
            raise RuntimeError("insert failed")
        self.session.data["members"][(oid, mode)] = duration

    async def update_fields(self, key, fields):
        self.session.data["members"][key] = fields["duration"]

    async def update(self, oid, fields):
        self.session.data["status"] = fields["status"]

    async def create(self, *args):
        self.session.data[self.kind] += 1
        return ACTIVITY


class Log:
    def __init__(self):
        self.texts = []

    def with_text(self, text, *args, **kwargs):
        self.texts.append(text)
        return self

    async def insert_log(self, session):
        session.data["logs"] += 1


@pytest.fixture
def store(monkeypatch):
    state = SimpleNamespace(data={"members": {}, "status": "effective", "activities": 0, "imports": 0, "logs": 0},
                            lock=asyncio.Lock(), fail_on=None, unknown=set(), bulk_calls=[])

    @asynccontextmanager
    async def scope():
        yield Session(state)

    monkeypatch.setattr(activities, "session_scope", scope)
    monkeypatch.setattr(imports, "session_scope", scope)
    for name in ("users", "members", "activities", "imports"):
        monkeypatch.setattr(backends, f"get_{name}_repo", lambda session, kind=name: Repos(session, kind))
    return state


def member(oid=USER, duration=1):
    return activities.NewActivityMember(member=oid, mode="on-campus", duration=duration)


@pytest.mark.asyncio
async def test_concurrent_member_increments_serialize_on_activity(store):
    await asyncio.gather(*(
        activities.add_activity_member_v3(ACTIVITY, [member(duration=value)], {"id": USER, "per": ["admin"]}, Log())
        for value in (1, 2, 3)
    ))
    assert store.data["members"][(USER, "on-campus")] == 6
    assert store.data["status"] == "pending"
    assert store.data["logs"] == 3


@pytest.mark.asyncio
async def test_later_member_failure_rolls_back_earlier_member_and_activity(store):
    store.fail_on = OTHER
    before = deepcopy(store.data)
    with pytest.raises(RuntimeError):
        await activities.add_activity_member_v3(ACTIVITY, [member(), member(OTHER)], {"id": USER, "per": ["admin"]}, Log())
    assert store.data == before


def workbook(rows):
    buffer = BytesIO()
    pd.DataFrame(rows, columns=imports._COLUMNS).to_excel(buffer, index=False)
    return buffer.getvalue()


@pytest.mark.parametrize("duration", [-1, "inf", "NaN", "invalid"])
def test_import_rejects_invalid_duration_before_writes(duration):
    with pytest.raises(ValueError):
        imports._parse_import(workbook([[USER, "Name", "123", "Group", duration, 0, 0]]))


def test_import_merges_duplicate_records_and_accepts_blank_durations():
    records = imports._parse_import(workbook([
        [USER, "Name", "123", "Group", 1, None, None],
        [USER, "Name", "123", "Group", 2, 0, 0],
    ]))
    assert records == {(USER, "on-campus"): 3}


def test_text_nan_in_mixed_workbook_cannot_be_treated_as_blank():
    with pytest.raises(ValueError):
        imports._parse_import(workbook([
            [USER, "Name", "123", "Group", 1, None, None],
            [OTHER, "Other", "124", "Group", "NaN", 0, 0],
        ]))


@pytest.mark.asyncio
async def test_failed_import_is_atomic(store):
    store.fail_on = OTHER
    content = workbook([[USER, "Name", "123", "Group", 1, 0, 0], [OTHER, "Name", "124", "Group", 1, 0, 0]])
    upload = UploadFile(BytesIO(content), filename="import.xlsx", headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}))
    before = deepcopy(store.data)
    with pytest.raises(RuntimeError):
        await imports.upload_activity_excel("义工活动", "测试活动导入描述", upload, {"id": USER, "per": ["admin"]}, Log())
    assert store.data == before


@pytest.mark.asyncio
async def test_import_requires_admin_before_reading_upload():
    with pytest.raises(HTTPException) as failure:
        await imports.upload_activity_excel("Activity", "Description", None, {"id": USER, "per": ["student"]}, Log())
    assert failure.value.status_code == 403


@pytest.mark.asyncio
async def test_import_log_uses_names_instead_of_oids(store):
    content = workbook([[USER, "Name", "123", "Group", 1, 0, 0]])
    upload = UploadFile(
        BytesIO(content),
        filename="import.xlsx",
        headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    )
    log = Log()
    await imports.upload_activity_excel("支教活动", "测试活动导入描述", upload, {"id": USER, "per": ["admin"]}, log)
    assert log.texts == ["用户 用户01 导入义工活动《支教活动》Excel，记录 1 条"]
    assert USER not in log.texts[0]


@pytest.mark.asyncio
async def test_import_checks_user_existence_in_batches(store):
    oids = [f"{index:024x}" for index in range(1, 1202)]
    content = workbook([[oid, "Name", "123", "Group", 1, 0, 0] for oid in oids])
    upload = UploadFile(
        BytesIO(content),
        filename="import.xlsx",
        headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    )
    await imports.upload_activity_excel("义工活动", "测试活动导入描述", upload, {"id": USER, "per": ["admin"]}, Log())

    batches = store.bulk_calls
    assert [len(batch) for batch in batches] == [500, 500, 201]
    assert sorted(oid for batch in batches for oid in batch) == oids


@pytest.mark.asyncio
async def test_import_unknown_user_still_rejects_the_whole_workbook(store):
    store.unknown = {OTHER}
    content = workbook([[USER, "Name", "123", "Group", 1, 0, 0], [OTHER, "Name", "124", "Group", 1, 0, 0]])
    upload = UploadFile(
        BytesIO(content),
        filename="import.xlsx",
        headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    )
    before = deepcopy(store.data)
    with pytest.raises(HTTPException) as failure:
        await imports.upload_activity_excel("义工活动", "测试活动导入描述", upload, {"id": USER, "per": ["admin"]}, Log())
    assert failure.value.status_code == 400
    assert store.data == before
