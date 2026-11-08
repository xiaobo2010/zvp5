"""OCR 引擎适配器 / 注册表 / 仓储 / 站点额度的离线契约。

三块各自钉一件事，都是「写错了不会报错、只会在生产上静默出错」的形状：

* **归一化**：上层只依赖 ``text`` / ``lines`` / ``engine`` / ``raw``，所以置信度
  必须统一到 ``0..1``（腾讯给 0-100，除错一次不会有人发现，只会让排序悄悄坏掉）、
  ``box`` 允许 ``None``、空结果算成功而不是异常。SDK 层用 monkeypatch 替换
  ``_load_*_sdk``（延迟导入的那一层），断言**入参组装**（base64 无前缀、
  ``LanguageType=zh``、阿里 body 是二进制）与出参归一化。
* **注册表 / 站点额度**：注册表只有五个云端 kind；站点额度落到
  ``SHARED_OWNER_OID`` + ``ocr_<engine>``，非白名单 kind 必须拒绝（拼错 = 额度记到
  一行永远不会被查的记录上，等于没扣）。
* **仓储**：SQL 形状按 ``literal_binds`` 编译后断言——排序键、enabled 过滤、
  「``None`` = 不改」的 UPDATE 语义，以及崩溃式错误（撞名）必须抛而不是吞。

不连库、不建引擎、不碰网络：所有上游都是假对象。
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import oracle
from sqlalchemy.exc import IntegrityError

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from db.repos import protocols
from db.repos.oracle import ai_ocr_engines as ocr_repo
from db.repos.protocols import SHARED_OWNER_OID
from services.ai import ocr_engines, quota
from services.ai.config import DEFAULT_QUOTA

DIALECT = oracle.dialect()
USER_OID = SHARED_OWNER_OID
TS = datetime(2026, 10, 23, 1, 2, 3)
PNG = b"\x89PNG\r\n\x1a\n fake-bytes-for-adapter-tests"


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


# ---------------------------------------------------------------------------
# 腾讯：假 SDK（替换延迟导入层）
# ---------------------------------------------------------------------------


class _FakeHttpProfile:
    reqTimeout = 60


class _FakeClientProfile:
    def __init__(self) -> None:
        self.httpProfile = _FakeHttpProfile()


class _FakeGeneralBasicOCRRequest:
    """腾讯 SDK 的 request 是「空对象 + 赋值」风格，这里保持一致。"""


class _FakeTencentModels:
    GeneralBasicOCRRequest = _FakeGeneralBasicOCRRequest


class _FakeTencentClient:
    def __init__(self, credential, region, profile) -> None:
        self.credential = credential
        self.region = region
        self.profile = profile
        self.request = None
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    async def GeneralBasicOCR(self, request):
        self.request = request
        return _tencent_response()


class _FakeCredential:
    def __init__(self, secret_id, secret_key) -> None:
        self.secret_id = secret_id
        self.secret_key = secret_key


def _tencent_response():
    detections = [
        SimpleNamespace(
            DetectedText="发票号码",
            Confidence=99,
            ItemPolygon=SimpleNamespace(X=1, Y=2, Width=30, Height=12),
        ),
        SimpleNamespace(DetectedText="12345", Confidence=88.5, ItemPolygon=None),
        # 有些响应行没有置信度：上层必须能容忍 None。
        SimpleNamespace(DetectedText="合计", Confidence=None, ItemPolygon=None),
    ]
    return SimpleNamespace(
        TextDetections=detections, RequestId="req-1", Language="zh"
    )


def _tencent_sdk(client_holder: dict | None = None) -> SimpleNamespace:
    client_cls = _FakeTencentClient
    if client_holder is not None:
        class _Recording(_FakeTencentClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                client_holder["client"] = self

        client_cls = _Recording
    return SimpleNamespace(
        Credential=_FakeCredential,
        ClientProfile=_FakeClientProfile,
        HttpProfile=_FakeHttpProfile,
        models=_FakeTencentModels,
        client_cls=client_cls,
    )


TENCENT_SECRET = {
    "secret_id": "fake-secret-id",
    "secret_key": "fake-secret-key",
    "region": "ap-guangzhou",
}


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_tencent_normalizes_to_unified_shape(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_tencent_sdk", lambda: _tencent_sdk(holder))

    result = _run(
        ocr_engines.TencentOcrEngine().recognize(
            PNG, "image/png", TENCENT_SECRET, {}
        )
    )

    assert set(result) == {"text", "lines", "engine", "raw"}
    assert result["engine"] == ocr_engines.KIND_TENCENT
    assert result["text"] == "发票号码\n12345\n合计"
    assert [line["text"] for line in result["lines"]] == ["发票号码", "12345", "合计"]
    # 腾讯的 Confidence 是 0-100，归一化后是 0-1。
    assert result["lines"][0]["confidence"] == pytest.approx(0.99)
    assert result["lines"][1]["confidence"] == pytest.approx(0.885)
    assert result["lines"][2]["confidence"] is None
    # box 保留厂商原始键名，不强转格式；缺失时是 None。
    assert result["lines"][0]["box"] == {"X": 1, "Y": 2, "Width": 30, "Height": 12}
    assert result["lines"][1]["box"] is None
    assert result["raw"]["RequestId"] == "req-1"
    assert len(result["raw"]["TextDetections"]) == 3
    assert holder["client"].closed is True  # httpx.AsyncClient 必须被关掉


def test_tencent_request_assembly_has_no_data_url_prefix(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_tencent_sdk", lambda: _tencent_sdk(holder))

    _run(
        ocr_engines.TencentOcrEngine().recognize(PNG, "image/png", TENCENT_SECRET, {})
    )

    client = holder["client"]
    assert client.request.ImageBase64 == base64.b64encode(PNG).decode("ascii")
    assert not client.request.ImageBase64.startswith("data:")
    assert client.request.LanguageType == "zh"
    assert client.region == "ap-guangzhou"
    assert client.credential.secret_id == "fake-secret-id"
    # 上游超时必须被显式收紧到 30 秒（默认 60 太长）。
    assert client.profile.httpProfile.reqTimeout == ocr_engines.DEFAULT_TIMEOUT


def test_tencent_strips_data_url_if_caller_passes_one(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_tencent_sdk", lambda: _tencent_sdk(holder))
    raw_b64 = base64.b64encode(PNG).decode("ascii")
    data_url = f"data:image/png;base64,{raw_b64}"

    _run(
        ocr_engines.TencentOcrEngine().recognize(
            data_url, "image/png", TENCENT_SECRET, {}
        )
    )

    assert holder["client"].request.ImageBase64 == raw_b64


def test_tencent_missing_credentials_raise_engine_error(monkeypatch) -> None:
    monkeypatch.setattr(ocr_engines, "_load_tencent_sdk", lambda: _tencent_sdk())

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _run(
            ocr_engines.TencentOcrEngine().recognize(
                PNG, "image/png", {"secret_id": "id"}, {}
            )
        )
    # 消息里只许出现字段名，绝不能带 secret 的值。
    assert "secret_key" in str(excinfo.value)
    assert "id" not in str(excinfo.value).replace("secret_id", "")


# ---------------------------------------------------------------------------
# 阿里：假 SDK（同步客户端 + to_thread）
# ---------------------------------------------------------------------------

ALIYUN_DATA = {
    "content": "合计 128.00",
    "prism_wordsInfo": [
        {"word": "合计", "prob": 0.98, "pos": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]},
        {"word": "128.00", "prob": 0.995, "pos": [{"x": 5, "y": 6}, {"x": 7, "y": 8}]},
    ],
}


class _FakeAliyunResponse:
    def __init__(self, data) -> None:
        self.body = SimpleNamespace(data=data)


class _FakeAliyunClient:
    response_data = json.dumps(ALIYUN_DATA, ensure_ascii=False)

    def __init__(self, config) -> None:
        self.config = config
        self.request = None
        self.runtime = None
        holder = _FakeAliyunClient.holder
        if holder is not None:
            holder["client"] = self

    holder: dict | None = None

    def recognize_general_with_options(self, request, runtime):
        self.request = request
        self.runtime = runtime
        return _FakeAliyunResponse(_FakeAliyunClient.response_data)


class _FakeRecognizeGeneralRequest:
    def __init__(self, url=None, body=None) -> None:
        self.url = url
        self.body = body


class _FakeAliyunModels:
    RecognizeGeneralRequest = _FakeRecognizeGeneralRequest


class _FakeOpenApiConfig:
    def __init__(self, access_key_id=None, access_key_secret=None) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.endpoint = None


class _FakeRuntimeOptions:
    read_timeout = None
    connect_timeout = None


def _aliyun_sdk(holder: dict | None = None) -> SimpleNamespace:
    _FakeAliyunClient.holder = holder
    return SimpleNamespace(
        Client=_FakeAliyunClient,
        models=_FakeAliyunModels,
        open_api_models=SimpleNamespace(Config=_FakeOpenApiConfig),
        util_models=SimpleNamespace(RuntimeOptions=_FakeRuntimeOptions),
    )


ALIYUN_SECRET = {
    "access_key_id": "fake-ak",
    "access_key_secret": "fake-sk",
}


def test_aliyun_normalizes_and_parses_nested_json_data(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_aliyun_sdk", lambda: _aliyun_sdk(holder))

    result = _run(
        ocr_engines.AliyunOcrEngine().recognize(PNG, "image/png", ALIYUN_SECRET, {})
    )

    assert result["engine"] == ocr_engines.KIND_ALIYUN
    assert result["text"] == "合计\n128.00"
    assert [line["text"] for line in result["lines"]] == ["合计", "128.00"]
    # 阿里的 prob 已经是 0-1，不能再除 100。
    assert result["lines"][0]["confidence"] == pytest.approx(0.98)
    assert result["lines"][1]["confidence"] == pytest.approx(0.995)
    # pos 原样保留（四个点），不做格式统一。
    assert result["lines"][0]["box"] == ALIYUN_DATA["prism_wordsInfo"][0]["pos"]
    assert result["raw"]["content"] == "合计 128.00"


def test_aliyun_request_sends_raw_bytes_and_sets_timeout(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_aliyun_sdk", lambda: _aliyun_sdk(holder))

    _run(
        ocr_engines.AliyunOcrEngine().recognize(PNG, "image/png", ALIYUN_SECRET, {})
    )

    client = holder["client"]
    # body 二选一时走 body：不吃「上游要能公网抓到图」的隐性前提。
    assert client.request.body == PNG
    assert client.request.url is None
    assert client.runtime.read_timeout == ocr_engines.DEFAULT_TIMEOUT
    assert client.config.endpoint == ocr_engines.ALIYUN_DEFAULT_ENDPOINT
    assert client.config.access_key_id == "fake-ak"


def test_aliyun_endpoint_override_from_secret(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_aliyun_sdk", lambda: _aliyun_sdk(holder))
    secret = dict(ALIYUN_SECRET, endpoint="ocr-api.cn-shanghai.aliyuncs.com")

    _run(ocr_engines.AliyunOcrEngine().recognize(PNG, "image/png", secret, {}))

    assert holder["client"].config.endpoint == "ocr-api.cn-shanghai.aliyuncs.com"


def test_aliyun_non_json_data_is_an_empty_result(monkeypatch) -> None:
    holder: dict = {}
    monkeypatch.setattr(ocr_engines, "_load_aliyun_sdk", lambda: _aliyun_sdk(holder))
    monkeypatch.setattr(_FakeAliyunClient, "response_data", "<html>gateway error</html>")

    result = _run(
        ocr_engines.AliyunOcrEngine().recognize(PNG, "image/png", ALIYUN_SECRET, {})
    )

    assert result["text"] == ""
    assert result["lines"] == []
    assert result["raw"]["data"] == "<html>gateway error</html>"


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_registry_covers_every_ddl_kind() -> None:
    # 两处都写死五元组：只断言 ``set(ENGINES) == set(ENGINE_KINDS)`` 时，两边
    # 一起漏掉同一个 kind 会静默通过——那正好是「DDL 拒收新 kind」的前置条件。
    expected_kinds = {
        ocr_engines.KIND_TENCENT,
        ocr_engines.KIND_ALIYUN,
        ocr_engines.KIND_BAIDU,
        ocr_engines.KIND_GROQ,
        ocr_engines.KIND_ZHIPU,
    }
    assert len(expected_kinds) == 5
    assert set(ocr_engines.ENGINE_KINDS) == expected_kinds
    assert set(ocr_engines.ENGINES) == expected_kinds
    for kind, engine_cls in ocr_engines.ENGINES.items():
        assert engine_cls.kind == kind
        assert callable(engine_cls().recognize)


def test_registry_kinds_match_the_model_check_constraint() -> None:
    """kind 与 ``ck_ai_ocr_engines_kind`` 必须逐字一致，否则插入直接被约束拒。"""
    from db.models import ai_ocr_engines as table

    checks = [
        str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name == "ck_ai_ocr_engines_kind"
    ]
    assert checks, "缺少 ck_ai_ocr_engines_kind"
    for kind in ocr_engines.ENGINE_KINDS:
        assert f"'{kind}'" in checks[0]


# ---------------------------------------------------------------------------
# 站点级额度
# ---------------------------------------------------------------------------


class _FakeQuotaRepo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.refunds: list[tuple] = []

    async def consume(self, user_oid, period, kind, amount, default_limit):
        self.calls.append((user_oid, period, kind, amount, default_limit))
        return True, 7, 100

    async def refund(self, user_oid, period, kind, amount):
        self.refunds.append((user_oid, period, kind, amount))
        return 1


def test_site_kinds_are_the_engine_whitelist() -> None:
    assert quota.SITE_KIND_PREFIX == "ocr_"
    # 白名单必须由注册表**派生**：只把字面量从 3 个换成 5 个，会让「新增引擎但
    # 忘了同步额度口径」继续通过；这里同时钉住派生关系与具体五元组。
    assert quota.SITE_KINDS == tuple(
        f"{quota.SITE_KIND_PREFIX}{kind}" for kind in ocr_engines.ENGINE_KINDS
    )
    assert set(quota.SITE_KINDS) == {
        "ocr_tencent",
        "ocr_aliyun",
        "ocr_baidu",
        "ocr_groq",
        "ocr_zhipu",
    }
    for kind in ocr_engines.ENGINE_KINDS:
        assert quota.site_kind(kind) == f"{quota.SITE_KIND_PREFIX}{kind}"
    assert quota.site_kind("tencent") == "ocr_tencent"


def test_site_kind_rejects_unknown_engine() -> None:
    for bad in ("openai", "ocr_tencent", "", None, 3):
        with pytest.raises(quota.QuotaError) as excinfo:
            quota.site_kind(bad)
        assert excinfo.value.code == "AI_QUOTA_BAD_KIND"


def test_site_consume_books_against_the_shared_owner(monkeypatch) -> None:
    repo = _FakeQuotaRepo()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: repo)

    allowed, used, limit = _run(quota.site_consume("aliyun", 2))

    # (7, 100) 来自假仓储那一行：consume 返回的是**行里的**上限，与默认值无关。
    assert (allowed, used, limit) == (True, 7, 100)
    user_oid, period, kind, amount, default_limit = repo.calls[0]
    # 站点额度不属于任何用户，用 24 个 0 的哨兵（NULL 会让唯一索引失效）。
    assert user_oid == SHARED_OWNER_OID == "0" * 24
    assert period == quota.period_now()
    assert kind == "ocr_aliyun"
    assert amount == 2
    # 传给仓储的 default_limit 必须是**站点口径**：回落到用户额度（100）会让站点
    # 额度一建就满，几十个用户一天打满、之后全站 OCR 503（2026-10-24 上线实测）。
    assert default_limit == quota._default_limit("ocr_aliyun")
    assert default_limit != DEFAULT_QUOTA["af"]


def test_site_consume_rejects_unknown_engine_before_touching_the_repo(monkeypatch) -> None:
    repo = _FakeQuotaRepo()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: repo)

    with pytest.raises(quota.QuotaError):
        _run(quota.site_consume("tencent_typo"))

    assert repo.calls == []


def test_site_refund_books_against_the_shared_owner(monkeypatch) -> None:
    """补偿走同一条站点行：用户额度竞态失败时把刚占的那一份退回去。"""
    repo = _FakeQuotaRepo()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: repo)

    assert _run(quota.site_refund("tencent")) == 1

    user_oid, period, kind, amount = repo.refunds[0]
    assert user_oid == SHARED_OWNER_OID
    assert period == quota.period_now()
    assert kind == "ocr_tencent"
    assert amount == 1


def test_site_refund_can_be_skipped(monkeypatch) -> None:
    """``units <= 0`` = 没东西可退：不写库，也不报错。"""
    repo = _FakeQuotaRepo()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: repo)

    assert _run(quota.site_refund("tencent", 0)) == 0

    assert repo.refunds == []


def test_refund_statement_clamps_used_at_zero() -> None:
    """单条 UPDATE 里 ``used - n`` 不足时归零：表上有 ``used >= 0`` 的 CHECK。"""
    from db.repos.oracle import ai_quota as quota_repo

    sql = _sql(
        quota_repo._refund_stmt(
            SHARED_OWNER_OID, "202610", "ocr_tencent", 1, TS
        )
    )

    assert "UPDATE ai_quota SET used=CASE WHEN" in sql
    assert "ai_quota.used - 1" in sql
    assert "ELSE 0 END" in sql
    # 归属与 kind 都在 WHERE 里：退款只可能落到那一行。
    assert f"ai_quota.user_oid = '{SHARED_OWNER_OID}'" in sql
    assert "ai_quota.kind = 'ocr_tencent'" in sql


def test_default_limit_uses_site_quota_for_site_kinds() -> None:
    # 站点 kind 走站点口径（默认 1000），用户级 kind（V051 起只有 af）才走
    # DEFAULT_QUOTA。
    assert quota._default_limit("ocr_tencent") == 1000
    assert quota._default_limit("ocr_tencent") != DEFAULT_QUOTA["af"]
    assert quota._default_limit("af") == DEFAULT_QUOTA["af"]
    # 历史 kind 没有默认值：谁把调用点的 kind 写回 chat/ocr 会立刻得到 0。
    assert quota._default_limit("ocr") == 0
    assert quota._default_limit("chat") == 0
    assert quota._default_limit("unknown_kind") == 0


# ---------------------------------------------------------------------------
# 仓储：协议 + SQL 形状
# ---------------------------------------------------------------------------


def test_protocol_declares_the_full_engine_surface() -> None:
    assert issubclass(protocols.DuplicateOcrEngine, Exception)
    for name in ("list_all", "get_by_oid", "insert", "update", "delete"):
        assert hasattr(protocols.AIOcrEnginesRepo, name)
        assert hasattr(ocr_repo.OracleAIOcrEnginesRepo, name)
        assert getattr(ocr_repo.OracleAIOcrEnginesRepo, name).__doc__ is not None


def test_backends_getter_returns_the_oracle_repo() -> None:
    repo = backends.get_ai_ocr_engines_repo()
    assert isinstance(repo, ocr_repo.OracleAIOcrEnginesRepo)


def test_list_all_orders_by_priority_then_name() -> None:
    sql = _sql(ocr_repo._list_all_stmt(False))

    assert sql.startswith("SELECT ai_ocr_engines.oid, ")
    assert "ai_ocr_engines.priority ASC" in sql
    # 同优先级必须有第二排序键，否则行序由存储引擎决定（测试与线上会不一致）。
    assert sql.index("ai_ocr_engines.priority ASC") < sql.index(
        "ai_ocr_engines.name ASC"
    )


def test_list_all_enabled_only_filters_in_sql() -> None:
    enabled_sql = _sql(ocr_repo._list_all_stmt(True))
    all_sql = _sql(ocr_repo._list_all_stmt(False))

    where = enabled_sql.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
    assert "ai_ocr_engines.enabled = 1" in where
    # 管理员视图要看到停用的引擎，才可能把它打开。
    assert " WHERE " not in all_sql


def test_get_by_oid_is_a_primary_key_lookup() -> None:
    oid = "a" * 24
    sql = _sql(ocr_repo._select_stmt(oid))
    assert f"ai_ocr_engines.oid = '{oid}'" in sql


def test_insert_projects_every_column_and_binds_config_as_json() -> None:
    sql = _sql(
        ocr_repo._insert_stmt(
            oid="b" * 24,
            name="tencent-main",
            kind="tencent",
            config={"language_type": "zh"},
            secret_cipher="cipher-text",
            key_hint="abcd",
            enabled=True,
            priority=10,
            created_at=TS,
            updated_at=TS,
        )
    )

    assert sql.upper().startswith("INSERT INTO AI_OCR_ENGINES (")
    # config 必须是 JSON 字符串（不是 Python repr），否则 IS JSON 约束会拒。
    assert '\'{"language_type": "zh"}\'' in sql
    assert "'tencent-main'" in sql
    assert "'tencent'" in sql
    assert "'cipher-text'" in sql
    assert " 1, 10," in sql


def test_update_only_touches_the_columns_that_were_passed() -> None:
    sql = _sql(ocr_repo._update_stmt("c" * 24, {"enabled": 0}, TS))
    set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]

    assert set_clause.startswith("enabled=0, updated_at=")
    # 「None = 不改」：没传的列绝不能被写（写 NULL 会把凭证静默抹掉）。
    for column in ("name", "kind", "config_json", "secret_cipher", "priority"):
        assert column not in set_clause


def test_update_values_ignores_none_but_keeps_falsey_values() -> None:
    values = ocr_repo._update_values(name=None, enabled=False, priority=0)
    assert values == {"enabled": 0, "priority": 0}


def test_delete_targets_one_row_by_oid() -> None:
    sql = _sql(ocr_repo._delete_stmt("d" * 24))
    assert sql.upper().startswith("DELETE FROM AI_OCR_ENGINES")
    assert f"ai_ocr_engines.oid = '{'d' * 24}'" in sql


def test_config_dict_survives_lob_and_garbage() -> None:
    class _Lob:
        def read(self):
            return json.dumps({"language_type": "en"})

    assert ocr_repo._config_dict(_Lob()) == {"language_type": "en"}
    assert ocr_repo._config_dict('{"a": 1}') == {"a": 1}
    assert ocr_repo._config_dict(None) == {}
    assert ocr_repo._config_dict("not-json") == {}
    assert ocr_repo._config_dict("[1, 2]") == {}


# ---------------------------------------------------------------------------
# 仓储：session 归属 + 撞名
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Row:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Session:
    def __init__(self, *, result=None, integrity: bool = False) -> None:
        self.result = result or _Result(rowcount=1)
        self.integrity = integrity
        self.statements: list = []
        self.commits = 0

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt):
        if self.integrity:
            raise IntegrityError("INSERT INTO ai_ocr_engines", {}, Exception("ORA-00001"))
        self.statements.append(stmt)
        return self.result

    async def commit(self) -> None:
        self.commits += 1


def _row_mapping(**overrides) -> dict:
    mapping = {
        "oid": "e" * 24,
        "name": "tencent-local",
        "kind": "tencent",
        "config_json": '{"language_type": "zh"}',
        "secret_cipher": None,
        "key_hint": None,
        "enabled": 1,
        "priority": 50,
        "created_at": TS,
        "updated_at": TS,
    }
    mapping.update(overrides)
    return mapping


def test_rows_are_mongo_shaped_dicts() -> None:
    session = _Session(result=_Result(rows=[_Row(_row_mapping())]))

    rows = _run(ocr_repo.OracleAIOcrEnginesRepo(session).list_all())

    assert rows == [
        {
            "_id": "e" * 24,
            "name": "tencent-local",
            "kind": "tencent",
            "config": {"language_type": "zh"},
            "secret_cipher": None,
            "key_hint": None,
            "enabled": True,
            "priority": 50,
            "created_at": TS,
            "updated_at": TS,
        }
    ]


def test_injected_session_is_neither_committed_nor_closed() -> None:
    session = _Session()
    repo = ocr_repo.OracleAIOcrEnginesRepo(session)

    assert _run(repo.update("e" * 24, priority=10)) is True
    assert _run(repo.delete("e" * 24)) is True
    assert _run(repo.insert(name="x", kind="tencent")) is not None
    # 注入的 session 归调用方：仓储自己 commit 会把上层事务切两半。
    assert session.commits == 0


def test_repo_created_session_commits_once(monkeypatch) -> None:
    session = _Session()

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(ocr_repo, "session_scope", lambda: _scope())

    oid = _run(ocr_repo.OracleAIOcrEnginesRepo().insert(name="x", kind="tencent"))

    assert oid is not None and len(oid) == 24
    assert session.commits == 1


def test_duplicate_name_raises_instead_of_being_swallowed() -> None:
    session = _Session(integrity=True)

    with pytest.raises(protocols.DuplicateOcrEngine) as excinfo:
        _run(ocr_repo.OracleAIOcrEnginesRepo(session).insert(name="dup", kind="tencent"))

    assert excinfo.value.name == "dup"
    assert session.commits == 0


def test_update_reports_miss_when_no_row_matched() -> None:
    session = _Session(result=_Result(rowcount=0))
    assert _run(ocr_repo.OracleAIOcrEnginesRepo(session).update("f" * 24, enabled=False)) is False
