"""OCR 上传图保留期清理（``purge_ocr_uploads`` + ``tasks/ocr_upload_gc.py``）。

钉的是**删除口径**——这条链上一旦写错就是不可逆的数据丢失，所以用真 SQLite
跑真删（不是断言 SQL 字符串），只有表结构换成 SQLite 能编译的同名列替身
（Oracle 的 ``VARCHAR2``/``TIMESTAMP``/``BLOB`` 在 SQLite 上编译不了，
``tests/test_purge_device_bindings.py`` 是同一套做法）。

覆盖：

* 只删「活动无关 + 未裁决 + 超期 + volunteer_order 原图」；活动图、已裁决图、
  未超期图、其它 purpose 一律保留；
* 被 non-thumb 子行引用的原图不删（无 FK 可级联，删了就是悬挂引用）；
* thumb / FILE_OCR / FILE_EVENTS 同批清干净；
* 同 sha256 的共享不跨 owner：别人的同 hash 行按自己的口径独立判定，不受影响；
* 批大小夹紧 + 短批即停 + 批数封顶；
* 任务层：默认 3 天、config 覆盖、异常吞掉只记错误、已删计数不丢。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.dialects import oracle

from db.repos.oracle import files as files_repo
from db.repos.oracle.files import OracleFilesRepo
from tasks import ocr_upload_gc

OWNER_A = "64b7f0c2e1a3b4c5d6e7fa01"
OWNER_B = "64b7f0c2e1a3b4c5d6e7fa02"
CUTOFF = datetime(2026, 10, 20, 0, 0, 0)
OLD = CUTOFF - timedelta(days=1)
FRESH = CUTOFF + timedelta(days=1)

_oid_seq = 0


def _oid(tag: str = "") -> str:
    """确定性 24-hex oid（``normalize_oid`` 只接受这个形状）。"""
    global _oid_seq
    _oid_seq += 1
    body = f"{_oid_seq:04x}{tag}".lower().encode()
    return (body.hex() + "0" * 24)[:24]


# ---------------------------------------------------------------------------
# SQLite 替身：同名列、SQLite 能编译的类型
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows, rowcount: int = -1) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0][0]


class _Session:
    """最小 async 外壳：``purge_ocr_uploads`` 只用到 execute/commit。"""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def execute(self, stmt):
        result = self._conn.execute(stmt)
        rows = result.fetchall() if result.returns_rows else []
        return _Result(rows, result.rowcount)

    async def commit(self) -> None:
        self._conn.commit()


def _table(name: str, meta: MetaData, *columns) -> Table:
    return Table(
        name,
        meta,
        Column("oid", String(24), primary_key=True),
        *columns,
    )


@pytest.fixture
def db(monkeypatch):
    """三张表的 SQLite 替身 + 一个真删的仓储实例。"""
    meta = MetaData()
    files_tbl = _table(
        "files",
        meta,
        Column("owner_oid", String(24), nullable=True),
        Column("kind", String(16), nullable=False),
        Column("parent_oid", String(24), nullable=True),
        Column("purpose", String(32), nullable=True),
        Column("entity_type", String(32), nullable=True),
        Column("entity_oid", String(24), nullable=True),
        Column("status", String(16), nullable=False),
        Column("ocr_status", String(16), nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("created_at", DateTime, nullable=False),
    )
    ocr_tbl = _table(
        "file_ocr",
        meta,
        Column("file_oid", String(24), nullable=False),
    )
    events_tbl = _table(
        "file_events",
        meta,
        Column("file_oid", String(24), nullable=False),
    )

    # 仓储里所有语句都由模块级函数生成、在调用时查模块全局，所以换表就够了。
    monkeypatch.setattr(files_repo, "files_tbl", files_tbl)
    monkeypatch.setattr(files_repo, "file_ocr_tbl", ocr_tbl)
    monkeypatch.setattr(files_repo, "file_events_tbl", events_tbl)

    engine = create_engine("sqlite://")
    meta.create_all(engine)
    conn = engine.connect()

    repo = OracleFilesRepo(session=_Session(conn))

    class Handle:
        def __init__(self) -> None:
            self.repo = repo
            self.conn = conn
            self.files = files_tbl
            self.ocr = ocr_tbl
            self.events = events_tbl

        def insert_file(self, **values) -> str:
            oid = values.pop("oid", None) or _oid()
            row = {
                "oid": oid,
                "owner_oid": OWNER_A,
                "kind": "original",
                "parent_oid": None,
                "purpose": "volunteer_order",
                "entity_type": None,
                "status": "stored",
                "ocr_status": "done",
                "sha256": "a" * 64,
                "created_at": OLD,
            }
            row.update(values)
            conn.execute(insert(files_tbl).values(**row))
            conn.commit()
            return oid

        def insert_ocr(self, file_oid: str) -> str:
            oid = _oid()
            conn.execute(insert(ocr_tbl).values(oid=oid, file_oid=file_oid))
            conn.commit()
            return oid

        def insert_event(self, file_oid: str) -> str:
            oid = _oid()
            conn.execute(insert(events_tbl).values(oid=oid, file_oid=file_oid))
            conn.commit()
            return oid

        def oids(self, table: Table) -> set[str]:
            rows = conn.execute(select(table.c.oid)).all()
            return {str(row[0]) for row in rows}

        def file_oids(self) -> set[str]:
            return self.oids(files_tbl)

    handle = Handle()
    yield handle
    conn.close()
    engine.dispose()


def _upload(handle, **overrides) -> tuple[str, str]:
    """一张完整的 OCR 上传图：原图 + 缩略图 + OCR 行 + 上传事件。"""
    original = handle.insert_file(**overrides)
    thumb = handle.insert_file(
        kind="thumb",
        parent_oid=original,
        sha256="b" * 64,
        owner_oid=overrides.get("owner_oid", OWNER_A),
    )
    handle.insert_ocr(original)
    handle.insert_event(original)
    return original, thumb


# ---------------------------------------------------------------------------
# 删除口径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deletes_only_unbound_unsettled_expired_volunteer_uploads(db):
    expired = _upload(db)[0]
    activity = _upload(db, entity_type="activity", entity_oid=_oid())[0]
    verified = _upload(db, status="verified")[0]
    rejected = _upload(db, status="rejected")[0]
    fresh = _upload(db, created_at=FRESH)[0]
    other_purpose = _upload(db, purpose="report")[0]

    counts = await db.repo.purge_ocr_uploads(CUTOFF, 200)

    assert counts["originals"] == 1
    assert expired not in db.file_oids()
    for kept in (activity, verified, rejected, fresh, other_purpose):
        assert kept in db.file_oids(), kept


@pytest.mark.asyncio
async def test_thumbnails_and_side_tables_go_with_their_original(db):
    original, thumb = _upload(db)
    other, other_thumb = _upload(db, created_at=FRESH)
    other_ocr = db.insert_ocr(other)
    other_event = db.insert_event(other)

    counts = await db.repo.purge_ocr_uploads(CUTOFF, 200)

    assert counts == {"originals": 1, "thumbs": 1, "ocr_rows": 1, "events": 1}
    assert original not in db.file_oids()
    assert thumb not in db.file_oids()
    # 未超期那张的附属行必须原样留着。
    assert {other, other_thumb} <= db.file_oids()
    assert other_ocr in db.oids(db.ocr)
    assert other_event in db.oids(db.events)


@pytest.mark.asyncio
async def test_an_original_with_a_non_thumb_child_is_kept(db):
    """没有 FK 可级联：删了原图就会留下悬挂的 derived 行，宁可整张保留。"""
    original, thumb = _upload(db)
    derived = db.insert_file(kind="derived", parent_oid=original)

    counts = await db.repo.purge_ocr_uploads(CUTOFF, 200)

    assert counts["originals"] == 0
    assert {original, thumb, derived} <= db.file_oids()


@pytest.mark.asyncio
async def test_same_sha256_rows_are_judged_per_row(db):
    """去重只在本 owner 内命中，清理也必须逐行判定，不能按 sha 连坐。"""
    handle_sha = "c" * 64
    expired = _upload(db, owner_oid=OWNER_A, sha256=handle_sha)[0]
    kept = _upload(db, owner_oid=OWNER_B, sha256=handle_sha, status="verified")[0]
    kept_fresh = _upload(db, owner_oid=OWNER_B, sha256=handle_sha, created_at=FRESH)[0]

    await db.repo.purge_ocr_uploads(CUTOFF, 200)

    assert expired not in db.file_oids()
    assert {kept, kept_fresh} <= db.file_oids()


@pytest.mark.asyncio
async def test_thumb_rows_are_never_selected_on_their_own(db):
    """孤立缩略图（parent 已不在）不在口径内：只按 parent 连带删，不单挑。"""
    orphan_thumb = db.insert_file(kind="thumb", parent_oid=_oid(), sha256="d" * 64)

    counts = await db.repo.purge_ocr_uploads(CUTOFF, 200)

    assert counts["originals"] == 0
    assert orphan_thumb in db.file_oids()


# ---------------------------------------------------------------------------
# 分批
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_limit_caps_one_call(db):
    for _ in range(3):
        _upload(db)

    first = await db.repo.purge_ocr_uploads(CUTOFF, 2)
    assert first["originals"] == 2
    assert len(db.file_oids()) == 2  # 剩一张原图 + 它的缩略图

    second = await db.repo.purge_ocr_uploads(CUTOFF, 2)
    assert second["originals"] == 1

    third = await db.repo.purge_ocr_uploads(CUTOFF, 2)
    assert third == {"originals": 0, "thumbs": 0, "ocr_rows": 0, "events": 0}


@pytest.mark.asyncio
async def test_oversized_batch_limit_is_clamped(db):
    for _ in range(3):
        _upload(db)

    # 请求 1000 张，仓储必须夹到 200（Oracle 的 IN 列表不允许无界扩张）。
    assert files_repo._clamp_batch_limit(1000) == 200
    assert files_repo._clamp_batch_limit(0) == 1
    assert files_repo._clamp_batch_limit(-5) == 1
    assert files_repo._clamp_batch_limit("junk") == 200

    counts = await db.repo.purge_ocr_uploads(CUTOFF, 1000)
    assert counts["originals"] == 3


@pytest.mark.asyncio
async def test_nothing_expired_leaves_every_table_untouched(db):
    _upload(db, created_at=FRESH)

    counts = await db.repo.purge_ocr_uploads(CUTOFF, 200)

    assert counts == {"originals": 0, "thumbs": 0, "ocr_rows": 0, "events": 0}
    assert len(db.file_oids()) == 2
    assert len(db.oids(db.ocr)) == 1
    assert len(db.oids(db.events)) == 1


# ---------------------------------------------------------------------------
# Oracle 侧 SQL 形状（不连库，只编译）
# ---------------------------------------------------------------------------


def test_oracle_sql_carries_the_whole_predicate():
    """口径写死一遍，防「SQLite 跑得过、Oracle 少一个条件」。"""
    stmt = files_repo._ocr_upload_candidates_stmt(CUTOFF, 200)
    sql = " ".join(
        str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).split()
    )

    assert "files.purpose = 'volunteer_order'" in sql
    assert "files.entity_type IS NULL" in sql
    assert "files.kind = 'original'" in sql
    assert f"files.created_at < TO_DATE('{CUTOFF.isoformat(sep=' ')}'" in sql
    assert "files.status NOT IN ('verified', 'rejected')" in sql
    assert "NOT (EXISTS (SELECT 1 FROM files child" in sql
    assert "child.parent_oid = files.oid" in sql
    assert "child.kind != 'thumb'" in sql
    assert "FETCH FIRST 200 ROWS ONLY" in sql


def test_oracle_originals_delete_reasserts_the_evidence_guards():
    """SELECT 与 DELETE 不是一条语句：删之前再核一次 kind/status。"""
    stmt = files_repo._delete_originals_stmt(["a" * 24])
    sql = " ".join(
        str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).split()
    )

    assert sql.startswith("DELETE FROM files WHERE")
    assert "files.oid IN ('aaaaaaaaaaaaaaaaaaaaaaaa')" in sql
    assert "files.kind = 'original'" in sql
    assert "files.status NOT IN ('verified', 'rejected')" in sql


# ---------------------------------------------------------------------------
# 任务层
# ---------------------------------------------------------------------------


class _StubRepo:
    def __init__(self, results: list[dict], raise_on: int | None = None) -> None:
        self.results = list(results)
        self.raise_on = raise_on
        self.calls: list[tuple[datetime, int]] = []

    async def purge_ocr_uploads(self, cutoff, batch_limit):
        self.calls.append((cutoff, batch_limit))
        if self.raise_on is not None and len(self.calls) - 1 == self.raise_on:
            raise RuntimeError("db down")
        return self.results.pop(0) if self.results else {
            "originals": 0,
            "thumbs": 0,
            "ocr_rows": 0,
            "events": 0,
        }


@pytest.fixture
def stub_backend(monkeypatch):
    def _install(results, raise_on=None):
        repo = _StubRepo(results, raise_on)
        monkeypatch.setattr(
            "db.backends.get_files_repo", lambda *a, **k: repo, raising=True
        )
        return repo

    return _install


def test_retention_defaults_to_three_days(monkeypatch):
    monkeypatch.delattr("config.OCR_UPLOAD_RETENTION_DAYS", raising=False)
    assert ocr_upload_gc.retention_days() == 3
    assert ocr_upload_gc.DEFAULT_OCR_UPLOAD_RETENTION_DAYS == 3
    assert ocr_upload_gc.batch_size() == 200
    assert ocr_upload_gc.max_batches() == 10


def test_retention_is_configurable_and_defensive(monkeypatch):
    monkeypatch.setattr("config.OCR_UPLOAD_RETENTION_DAYS", 7, raising=False)
    assert ocr_upload_gc.retention_days() == 7

    monkeypatch.setattr("config.OCR_UPLOAD_RETENTION_DAYS", "junk", raising=False)
    assert ocr_upload_gc.retention_days() == 3

    monkeypatch.setattr("config.OCR_UPLOAD_RETENTION_DAYS", 0, raising=False)
    assert ocr_upload_gc.retention_days() == 3


@pytest.mark.asyncio
async def test_cutoff_is_now_minus_retention(stub_backend):
    repo = stub_backend([{"originals": 0, "thumbs": 0, "ocr_rows": 0, "events": 0}])

    stats = await ocr_upload_gc.run_ocr_upload_gc()

    expected = datetime.now() - timedelta(days=3)
    cutoff = repo.calls[0][0]
    assert abs((cutoff - expected).total_seconds()) < 5
    assert stats["cutoff"] == cutoff.isoformat()
    assert stats["retention_days"] == 3


@pytest.mark.asyncio
async def test_short_batch_stops_the_loop(stub_backend, monkeypatch):
    monkeypatch.setattr(ocr_upload_gc, "DEFAULT_OCR_UPLOAD_GC_BATCH_SIZE", 5)
    repo = stub_backend(
        [
            {"originals": 2, "thumbs": 2, "ocr_rows": 2, "events": 2},
            {"originals": 5, "thumbs": 5, "ocr_rows": 5, "events": 5},
        ]
    )

    stats = await ocr_upload_gc.run_ocr_upload_gc()

    # 第一批 2 < 5 ⇒ 立即停，第二批根本不该发生。
    assert len(repo.calls) == 1
    assert stats["batches"] == 1
    assert stats["originals"] == 2


@pytest.mark.asyncio
async def test_loop_is_capped_ten_batches(monkeypatch):
    monkeypatch.setattr(ocr_upload_gc, "DEFAULT_OCR_UPLOAD_GC_BATCH_SIZE", 2)

    # 永远满批：只能靠批数封顶停下来。
    repo = _StubRepo([{"originals": 2, "thumbs": 2, "ocr_rows": 2, "events": 2}] * 50)
    monkeypatch.setattr("db.backends.get_files_repo", lambda *a, **k: repo)

    stats = await ocr_upload_gc.run_ocr_upload_gc()

    assert len(repo.calls) == 10
    assert stats["batches"] == 10
    assert stats["originals"] == 20


@pytest.mark.asyncio
async def test_failures_are_swallowed_and_counts_are_kept(stub_backend, monkeypatch):
    monkeypatch.setattr(ocr_upload_gc, "DEFAULT_OCR_UPLOAD_GC_BATCH_SIZE", 2)
    repo = stub_backend(
        [{"originals": 2, "thumbs": 2, "ocr_rows": 3, "events": 4}] * 3, raise_on=1
    )

    stats = await ocr_upload_gc.run_ocr_upload_gc()

    assert len(repo.calls) == 2  # 第二批炸了，没有第三批
    assert "error" in stats and "db down" in stats["error"]
    # 第一批真删掉的行照常计数。
    assert stats["originals"] == 2
    assert stats["thumbs"] == 2
    assert stats["ocr_rows"] == 3
    assert stats["events"] == 4
