"""AI 网关迁移的三方镜像：V024 / V025 / V026 / V027 / V028 / ``db/models.py`` /
V001，离线不连库。

``db.ddl_check`` 只比对 models 与 V001（``python -m db.ddl_check``），所以「V024、
V025、V026、V027、V028 是否真的与 models 一致」这条事实没有任何东西守着——它一
漂，存量实例（走 V024..V028）与全新实例（走 V001）就会长出不同的 AI 表。这里用
ddl_check 的解析器把这些迁移文件也拉进同一次比较。

迁移是历史事实：V024/V025/V026/V027 描述的是当时的形状，后续迁移只做增量。所以
下面的断言写成「V0xx == models **减去** 后续迁移的增量」，而不是让历史文件去追新
目标态——那样每次改 schema 都得回头改历史迁移。

版本号是写死的：迁移被改名/重编号本身就是运维要立刻知道的事，测试宁可红一次，
也不要在「文件找不到」时悄悄跳过整组断言。
"""

from __future__ import annotations

import re
from pathlib import Path

from db import ddl_check

DDL_DIR = ddl_check.DDL_PATH.parent

# AI 网关的迁移：V024 建表（ai_call_logs / ai_quota），V025 建凭据表并给
# ai_call_logs 补 key_scope，V026 建 OCR 引擎表并放宽 ai_quota.kind，V027 把
# ai_ocr_engines.kind 从三值扩到五值，V028 给 ai_credentials 补 expires_at。
GATEWAY_FILE = "V024__add_ai_gateway.sql"
CREDENTIALS_FILE = "V025__add_ai_credentials.sql"
OCR_FILE = "V026__add_ai_ocr_engines_and_key_headers.sql"
OCR_KIND_FILE = "V027__extend_ai_ocr_engines_kind.sql"
ZHIPU_KIND_FILE = "V029__add_zhipu_ocr_engine_kind.sql"
EXPIRY_FILE = "V028__add_ai_credentials_expires_at.sql"
API_KEYS_FILE = "V030__add_ai_api_keys.sql"
TRACE_FILE = "V031__add_ai_call_logs_trace.sql"
AGENT_FILE = "V033__agent_and_prompt_audit.sql"
PROPOSAL_FILE = "V034__agent_proposal_payload.sql"

# V033 给 ai_call_logs 补的两列（agent 轮次 + 角色快照），全部 nullable。
AGENT_TRACE_COLUMNS = {"round_no", "roles_snapshot"}
AGENT_TRACE_CHECK_EXPRS = {"round_no is null or round_no >= 1"}

# V034 给 ai_agent_events 补的四列（完整参数 / 预览 / 审批人 / 执行时间）与
# 放宽后的状态 CHECK。V033 建表时还没有这些，也没有 ``executing``。
PROPOSAL_COLUMNS = {"args_payload", "preview", "approver_oid", "confirmed_at"}
LEGACY_AGENT_STATUS_CK = (
    "status in ('executed','proposed','confirmed','rejected','expired',"
    "'failed','denied')"
)
AGENT_STATUS_CK = (
    "status in ('executed','proposed','executing','confirmed','rejected',"
    "'expired','failed','denied')"
)

# 相邻的 V032（prompt 前缀缓存）也往 ai_call_logs 补了三列 + 一个
# CHECK。两份迁移互不依赖（各自幂等）：比较 V024 的建表形状时两种增量都要摘掉。
CACHE_TRACE_COLUMNS = {"cache_read_tokens", "cache_write_tokens", "cache_key"}
CACHE_TRACE_CHECK_EXPRS = {
    "(cache_read_tokens is null or cache_read_tokens >= 0)"
    " and (cache_write_tokens is null or cache_write_tokens >= 0)"
}

# ``ai_call_logs.key_scope`` 的 CHECK 规范化后的形态（ddl_check 会把大小写与空白
# 折叠掉）。V024 建表时还没有这一列，它由 V025 的幂等 ALTER 补上——所以下面既断言
# 「models/V001 里有它」，也断言「V024 里没有它」，把这次拆分本身钉住。
KEY_SCOPE_CK = "key_scope is null or key_scope in ('personal','shared','server')"

# V031 给 ai_call_logs 补的 8 个 trace 列（全部 nullable、无默认值）与两个 CHECK。
TRACE_COLUMNS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "attempt_no",
    "purpose",
    "first_token_ms",
    "engine_kind",
    "attachment_oid",
}
TRACE_CHECK_EXPRS = {
    "(prompt_tokens is null or prompt_tokens >= 0)"
    " and (completion_tokens is null or completion_tokens >= 0)"
    " and (total_tokens is null or total_tokens >= 0)",
    "attempt_no is null or attempt_no >= 1",
}

GATEWAY_INDEXES = {
    ("ai_call_logs", False, ("user_oid", "created_at")),
    ("ai_call_logs", False, ("created_at",)),
    ("ai_call_logs", False, ("endpoint", "status", "created_at")),
    ("ai_quota", True, ("user_oid", "period", "kind")),
}

CREDENTIAL_INDEXES = {
    ("ai_credentials", True, ("scope", "owner_oid", "name")),
    ("ai_credentials", False, ("owner_oid", "enabled")),
    ("ai_credentials", False, ("scope", "enabled")),
}

# V024 建 ai_quota 时 KIND 是枚举，V026 把它放宽成前缀式（'ocr' / 'chat' /
# 'ocr_<engine>'）。两个表达式都写死在这里：它们是这次迁移的实质内容。
LEGACY_QUOTA_KIND_CK = "kind in ('ocr','chat')"
QUOTA_KIND_CK = "kind in ('ocr','chat') or kind like 'ocr\\_%' escape '\\'"

# V026 给 ai_credentials 补的两列与它们的 IS JSON 约束。
HEADER_COLUMNS = ("headers_json", "api_path")
HEADERS_JSON_CK = "headers_json is json"

# V028 给 ai_credentials 补的到期列（NULL = 永不过期）。
EXPIRY_COLUMN = "expires_at"

# ai_ocr_engines.kind 的三代集合：V026 建表时三值，V027 扩到五值（+baidu/groq），
# V029 再扩到六值（+zhipu）。三代都写死：迁移是历史事实，不能让旧迁移去追新态。
LEGACY_OCR_KIND_CK = "kind in ('tencent','aliyun','rapidocr')"
V027_OCR_KIND_CK = "kind in ('tencent','aliyun','rapidocr','baidu','groq')"
OCR_KIND_CK = "kind in ('tencent','aliyun','rapidocr','baidu','groq','zhipu')"
OCR_KIND_CONSTRAINT = "ck_ai_ocr_engines_kind"


def _migration(name: str) -> Path:
    path = DDL_DIR / name
    assert path.is_file(), f"AI migration {name} is missing from {DDL_DIR}"
    return path


def _parsed(path: Path) -> dict:
    return ddl_check.parse_schema(
        ddl_check.extract_execute_immediate(path.read_text(encoding="utf-8"))
    )


def _gateway() -> dict:
    return _parsed(_migration(GATEWAY_FILE))


def _credentials() -> dict:
    return _parsed(_migration(CREDENTIALS_FILE))


def _gateway_text() -> str:
    return _migration(GATEWAY_FILE).read_text(encoding="utf-8")


def _trace() -> dict:
    return _parsed(_migration(TRACE_FILE))


def _trace_text() -> str:
    return _migration(TRACE_FILE).read_text(encoding="utf-8")


def _credentials_text() -> str:
    return _migration(CREDENTIALS_FILE).read_text(encoding="utf-8")


def _ocr() -> dict:
    return _parsed(_migration(OCR_FILE))


def _ocr_text() -> str:
    return _migration(OCR_FILE).read_text(encoding="utf-8")


def _ocr_kind_text() -> str:
    return _migration(OCR_KIND_FILE).read_text(encoding="utf-8")


def _zhipu_kind_text() -> str:
    return _migration(ZHIPU_KIND_FILE).read_text(encoding="utf-8")


def _expiry() -> dict:
    return _parsed(_migration(EXPIRY_FILE))


def _expiry_text() -> str:
    return _migration(EXPIRY_FILE).read_text(encoding="utf-8")


def _api_keys() -> dict:
    return _parsed(_migration(API_KEYS_FILE))


def _api_keys_text() -> str:
    return _migration(API_KEYS_FILE).read_text(encoding="utf-8")


def test_ai_quota_migration_matches_models_and_v001() -> None:
    gateway = _gateway()
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["ai_quota"]
    # V024 建表时 KIND 还是枚举；V026 才把它放宽成前缀式。
    before_widening = {
        "columns": expected["columns"],
        "pk": expected["pk"],
        "uq": set(expected["uq"]),
        "fk": set(expected["fk"]),
        "ck": (expected["ck"] - {QUOTA_KIND_CK}) | {LEGACY_QUOTA_KIND_CK},
    }
    assert gateway["tables"]["ai_quota"] == before_widening

    # 全新实例走 V001，一次就拿到放宽后的形状。
    assert v001["tables"]["ai_quota"] == expected
    assert QUOTA_KIND_CK in expected["ck"]

    # V026 重建的约束必须与 models/V001 的表达式逐字一致（含 ESCAPE）。
    extracted = ddl_check.extract_execute_immediate(_ocr_text())
    rebuilt = re.search(
        r"ALTER TABLE ai_quota ADD CONSTRAINT (\w+) CHECK \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert rebuilt, extracted
    parsed = ddl_check._parse_constraint(
        f"constraint {rebuilt.group(1)} check ({rebuilt.group(2)})"
    )
    assert parsed == ("ck", QUOTA_KIND_CK)


def test_ai_call_logs_migration_matches_models_minus_key_scope() -> None:
    gateway = _gateway()
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["ai_call_logs"]
    # V024 建表时还没有 key_scope（V025 补）、V031 的 8 个 trace 列与 V033 的 2 个
    # agent 列：把它们从 models 侧摘掉后必须与迁移逐字段相等。
    without_later = {
        "columns": {
            k: v
            for k, v in expected["columns"].items()
            if k != "key_scope"
            and k not in TRACE_COLUMNS
            and k not in AGENT_TRACE_COLUMNS
            and k not in CACHE_TRACE_COLUMNS
        },
        "pk": expected["pk"],
        "uq": set(expected["uq"]),
        "fk": set(expected["fk"]),
        "ck": {
            ck
            for ck in expected["ck"]
            if ck != KEY_SCOPE_CK
            and ck not in TRACE_CHECK_EXPRS
            and ck not in AGENT_TRACE_CHECK_EXPRS
            and ck not in CACHE_TRACE_CHECK_EXPRS
        },
    }
    assert gateway["tables"]["ai_call_logs"] == without_later

    # 全新实例走 V001，一次就拿到完整形状（含 key_scope 与 V031/V032/V033 的列）。
    assert v001["tables"]["ai_call_logs"] == expected
    assert "key_scope" in v001["tables"]["ai_call_logs"]["columns"]
    assert KEY_SCOPE_CK in v001["tables"]["ai_call_logs"]["ck"]
    for column in TRACE_COLUMNS | AGENT_TRACE_COLUMNS | CACHE_TRACE_COLUMNS:
        assert column in v001["tables"]["ai_call_logs"]["columns"]
    for check in (
        TRACE_CHECK_EXPRS | AGENT_TRACE_CHECK_EXPRS | CACHE_TRACE_CHECK_EXPRS
    ):
        assert check in v001["tables"]["ai_call_logs"]["ck"]


def test_trace_migration_adds_the_columns_the_models_declare() -> None:
    """V031 的 8 条 ADD 列 + 2 个 CHECK + 1 个索引必须与 models/V001 一致。"""
    extracted = ddl_check.extract_execute_immediate(_trace_text())
    expected_columns = ddl_check.parse_models()["tables"]["ai_call_logs"]["columns"]

    added = re.findall(
        r"ALTER TABLE ai_call_logs ADD \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert added, extracted
    for frag in added:
        name, type_, notnull, default = ddl_check._parse_column(frag)
        assert (type_, notnull, default) == expected_columns[name]
    # 全部 nullable 且没有默认值：存量行留 NULL，「未知」不该被 0 顶替。
    assert {frag.split()[0].lower() for frag in added} == set(TRACE_COLUMNS)
    for frag in added:
        _name, _type, notnull, default = ddl_check._parse_column(frag)
        assert notnull is False
        assert default is None

    constraints = re.findall(
        r"ALTER TABLE ai_call_logs ADD CONSTRAINT (\w+) CHECK \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert constraints, extracted
    parsed = {
        ddl_check._parse_constraint(f"constraint {name} check ({expr})")
        for name, expr in constraints
    }
    assert {expr for _kind, expr in parsed} == TRACE_CHECK_EXPRS
    assert TRACE_CHECK_EXPRS <= ddl_check.parse_v001()["tables"]["ai_call_logs"]["ck"]

    assert ("ai_call_logs", False, ("request_id",)) in _trace()["indexes"]
    assert ("ai_call_logs", False, ("request_id",)) in ddl_check.parse_models()[
        "indexes"
    ]


AGENT_TABLES = {"ai_agent_events", "ai_prompt_audit"}
AGENT_INDEXES = {
    ("ai_agent_events", False, ("request_id",)),
    ("ai_agent_events", False, ("status", "created_at")),
    ("ai_prompt_audit", False, ("user_oid", "created_at")),
}


def test_agent_migration_matches_models_and_v001() -> None:
    """V033 的两列 + 两张表 + 三个索引必须与 models/V001 逐字段一致。

    ``ai_agent_events`` 按文件头约定比较「models **减去** V034 的增量」：V033 是
    历史事实（当时没有 ARGS_PAYLOAD/PREVIEW/APPROVER_OID/CONFIRMED_AT，状态 CHECK
    也没有 ``executing``），后续迁移只做增量。
    """
    agent = _parsed(_migration(AGENT_FILE))
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    assert agent["tables"]["ai_prompt_audit"] == modeled["tables"]["ai_prompt_audit"]
    assert v001["tables"]["ai_prompt_audit"] == modeled["tables"]["ai_prompt_audit"]

    expected_events = modeled["tables"]["ai_agent_events"]
    before_proposal_v2 = {
        "columns": {
            name: spec
            for name, spec in expected_events["columns"].items()
            if name not in PROPOSAL_COLUMNS
        },
        "pk": expected_events["pk"],
        "uq": set(expected_events["uq"]),
        "fk": set(expected_events["fk"]),
        "ck": (expected_events["ck"] - {AGENT_STATUS_CK}) | {LEGACY_AGENT_STATUS_CK},
    }
    assert agent["tables"]["ai_agent_events"] == before_proposal_v2

    # 全新实例走 V001，一次就拿到完整形状（含 V034 的四列与 executing）。
    assert v001["tables"]["ai_agent_events"] == expected_events
    for column in PROPOSAL_COLUMNS:
        assert column in v001["tables"]["ai_agent_events"]["columns"]
    assert AGENT_STATUS_CK in expected_events["ck"]
    assert LEGACY_AGENT_STATUS_CK not in expected_events["ck"]

    assert AGENT_INDEXES <= agent["indexes"]
    assert AGENT_INDEXES <= modeled["indexes"]
    assert AGENT_INDEXES <= v001["indexes"]

    # 两张新表的 OID 是 32 位 hex（uuid4().hex），不是 24 位 ObjectId：不能带
    # ck_*_oid 的 24 位 REGEXP 约束，否则插入会直接被数据库拒掉。
    for table in AGENT_TABLES:
        assert modeled["tables"][table]["columns"]["oid"][0] == "varchar2(32)"
        assert not any("oid" in ck and "0-9a-f]{24}" in ck for ck in modeled["tables"][table]["ck"])


def test_agent_migration_widens_ai_call_logs_with_round_and_roles() -> None:
    extracted = ddl_check.extract_execute_immediate(_migration(AGENT_FILE).read_text(encoding="utf-8"))
    expected_columns = ddl_check.parse_models()["tables"]["ai_call_logs"]["columns"]

    added = re.findall(
        r"ALTER TABLE ai_call_logs ADD \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert added, extracted
    assert {frag.split()[0].lower() for frag in added} == AGENT_TRACE_COLUMNS
    for frag in added:
        name, type_, notnull, default = ddl_check._parse_column(frag)
        assert (type_, notnull, default) == expected_columns[name]
        assert notnull is False
        assert default is None

    constraints = re.findall(
        r"ALTER TABLE ai_call_logs ADD CONSTRAINT (\w+) CHECK \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    parsed = {
        ddl_check._parse_constraint(f"constraint {name} check ({expr})")
        for name, expr in constraints
    }
    assert {expr for _kind, expr in parsed} == AGENT_TRACE_CHECK_EXPRS


def test_proposal_migration_adds_the_columns_and_widens_the_status_check() -> None:
    """V034 的四条 ADD 列 + 重建后的状态 CHECK 必须与 models/V001 一致。"""
    text = _migration(PROPOSAL_FILE).read_text(encoding="utf-8")
    extracted = ddl_check.extract_execute_immediate(text)
    expected_columns = ddl_check.parse_models()["tables"]["ai_agent_events"]["columns"]

    added = re.findall(
        r"ALTER TABLE ai_agent_events ADD \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert added, extracted
    assert {frag.split()[0].lower() for frag in added} == PROPOSAL_COLUMNS
    for frag in added:
        name, type_, notnull, default = ddl_check._parse_column(frag)
        assert (type_, notnull, default) == expected_columns[name]
        # 存量行（活动域的执行事件、已确认的提案）没有这些信息：NULL 才是真话。
        assert notnull is False
        assert default is None

    # 先 DROP 再 ADD：只 ADD 会在约束名已被占用时被 -2264 吞掉，于是执行仍然
    # 缺 executing——这一条把顺序钉住。
    assert re.search(
        r"ALTER TABLE ai_agent_events DROP CONSTRAINT ck_ai_agent_events_status",
        extracted,
        re.IGNORECASE,
    )
    constraints = re.findall(
        r"ALTER TABLE ai_agent_events ADD CONSTRAINT (\w+) CHECK \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    parsed = [ddl_check._parse_constraint(f"constraint {name} check ({expr})")
              for name, expr in constraints]
    assert parsed == [("ck", AGENT_STATUS_CK)]

    # 幂等性：DROP 要吞 -2443（约束不存在），ADD 要吞 -2264（重跑）。
    assert "IF SQLCODE NOT IN (-2443, -2264, -955) THEN RAISE; END IF;" in text
    assert "IF SQLCODE NOT IN (-2264, -955) THEN RAISE; END IF;" in text
    # 历史迁移不得回头改：V034 之外的两份仍然描述各自的年代。
    v033 = _migration(AGENT_FILE).read_text(encoding="utf-8")
    assert "args_payload" not in v033
    assert "executing" not in v033


def test_seed_ai_chat_burst_is_mirrored_in_both_files() -> None:
    """``ai_chat_burst`` 只建种子：两份文件必须一致，且 mode 为 alert（不封人）。"""
    for path in (_migration(AGENT_FILE), DDL_DIR / "V001__initial_schema.sql"):
        text = ddl_check.extract_execute_immediate(path.read_text(encoding="utf-8"))
        assert "'ai_chat_burst'" in text, path.name
        assert (
            '\'ai_chat_burst\', 1, \'{"threshold":80,"window_hours":1,"mode":"alert"}\''
            in text
        ), path.name


def test_gateway_migration_carries_the_models_indexes() -> None:
    gateway = _gateway()
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    assert GATEWAY_INDEXES <= gateway["indexes"]
    assert GATEWAY_INDEXES <= modeled["indexes"]
    assert GATEWAY_INDEXES <= v001["indexes"]


def test_ai_credentials_migration_matches_models_and_v001() -> None:
    credentials = _credentials()
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["ai_credentials"]
    # V025 建表时还没有请求形状列（V026 补）与到期列（V028 补）。
    before_headers = {
        "columns": {
            k: v
            for k, v in expected["columns"].items()
            if k not in HEADER_COLUMNS and k != EXPIRY_COLUMN
        },
        "pk": expected["pk"],
        "uq": set(expected["uq"]),
        "fk": set(expected["fk"]),
        "ck": expected["ck"] - {HEADERS_JSON_CK},
    }
    assert credentials["tables"]["ai_credentials"] == before_headers

    # 全新实例走 V001，一次就拿到完整形状（含 headers_json / api_path /
    # expires_at）。
    assert v001["tables"]["ai_credentials"] == expected

    assert CREDENTIAL_INDEXES <= credentials["indexes"]
    assert CREDENTIAL_INDEXES <= modeled["indexes"]
    assert CREDENTIAL_INDEXES <= v001["indexes"]


def test_ocr_migration_adds_the_header_columns_the_models_declare() -> None:
    """V026 的两条 ADD 列 + IS JSON 约束必须与 models/V001 逐字段一致。"""
    extracted = ddl_check.extract_execute_immediate(_ocr_text())
    expected_columns = ddl_check.parse_models()["tables"]["ai_credentials"]["columns"]

    added = re.findall(
        r"ALTER TABLE ai_credentials ADD \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert added, extracted
    for frag in added:
        name, type_, notnull, default = ddl_check._parse_column(frag)
        assert (type_, notnull, default) == expected_columns[name]
    assert {frag.split()[0].lower() for frag in added} == set(HEADER_COLUMNS)

    constraint = re.search(
        r"ALTER TABLE ai_credentials ADD CONSTRAINT (\w+) CHECK \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert constraint, extracted
    parsed = ddl_check._parse_constraint(
        f"constraint {constraint.group(1)} check ({constraint.group(2)})"
    )
    assert parsed == ("ck", HEADERS_JSON_CK)
    assert parsed[1] in ddl_check.parse_v001()["tables"]["ai_credentials"]["ck"]


def test_ocr_migration_matches_models_and_v001() -> None:
    ocr = _ocr()
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["ai_ocr_engines"]
    # V026 建表时 kind 还是三值集合，V027 才补上 baidu/groq。按文件头约定，
    # V026 的期望形状 = models **减去** V027 的增量（而不是让历史迁移追新态）。
    before_kind_extension = {
        "columns": expected["columns"],
        "pk": expected["pk"],
        "uq": set(expected["uq"]),
        "fk": set(expected["fk"]),
        "ck": (expected["ck"] - {OCR_KIND_CK}) | {LEGACY_OCR_KIND_CK},
    }
    assert ocr["tables"]["ai_ocr_engines"] == before_kind_extension

    # 全新实例走 V001，一次就拿到五值集合。
    assert v001["tables"]["ai_ocr_engines"] == expected
    assert OCR_KIND_CK in expected["ck"]
    assert LEGACY_OCR_KIND_CK not in expected["ck"]

    indexes = {
        ("ai_ocr_engines", True, ("name",)),
        ("ai_ocr_engines", False, ("enabled", "priority")),
    }
    assert indexes <= ocr["indexes"]
    assert indexes <= modeled["indexes"]
    assert indexes <= v001["indexes"]


def test_v027_extends_kind_to_the_modeled_set() -> None:
    """V027 只做一件事：把 kind 的 CHECK 扩到 models/V001 的五值集合。

    三件事都必须钉住：约束名与表达式逐字一致、幂等是先读后改（重复执行 no-op）、
    以及历史迁移 V026 保持冻结（不得提前出现 baidu/groq）。
    """
    text = _ocr_kind_text()
    extracted = ddl_check.extract_execute_immediate(text)

    added = re.search(
        r"ALTER TABLE\s+ai_ocr_engines\s+ADD CONSTRAINT\s+(\w+)\s+CHECK\s*\((.*)\)",
        extracted,
        re.IGNORECASE | re.DOTALL,
    )
    assert added, extracted
    assert added.group(1).lower() == OCR_KIND_CONSTRAINT
    parsed = ddl_check._parse_constraint(
        f"constraint {added.group(1)} check ({added.group(2)})"
    )
    assert parsed == ("ck", V027_OCR_KIND_CK)

    # V027 是**历史态**：models/V001 现在已是六值（V029 加了 zhipu），所以这里不
    # 要求与当前集合逐字相等，而是钉住「五值该有的都有、六值里的 zhipu 还没有」。
    for literal in ("'tencent'", "'aliyun'", "'rapidocr'", "'baidu'", "'groq'"):
        assert literal in parsed[1]
    assert "'zhipu'" not in parsed[1]
    modeled_ck = ddl_check.parse_models()["tables"]["ai_ocr_engines"]["ck"]
    v001_ck = ddl_check.parse_v001()["tables"]["ai_ocr_engines"]["ck"]
    assert OCR_KIND_CK in modeled_ck
    assert OCR_KIND_CK in v001_ck
    # 表达式里真的带上两个新 kind（只比对长度/前缀会让漏一个也通过）。
    assert "'baidu'" in parsed[1]
    assert "'groq'" in parsed[1]

    # 幂等结构：先读 USER_CONSTRAINTS，只有当前表达式缺 baidu/groq 时才 DROP+ADD；
    # 已是新集合时整段 no-op（不会在重跑窗口里短暂摘掉约束）。
    lowered = text.lower()
    read_at = lowered.index("from user_constraints")
    needs_change_at = lowered.index("if v_needs_change then")
    drop_at = lowered.index("drop constraint ck_ai_ocr_engines_kind")
    add_at = lowered.index("add constraint ck_ai_ocr_engines_kind")
    assert read_at < needs_change_at < drop_at < add_at
    assert "select search_condition" in lowered
    assert "where table_name = 'ai_ocr_engines'" in lowered
    assert "and constraint_name = 'ck_ai_ocr_engines_kind'" in lowered
    assert "and constraint_type = 'c'" in lowered
    # 两个新字面量都参与「需不需要改」的判断：只查一个会让另一个永远补不上。
    assert "instr(lower(v_condition), '''baidu''') = 0" in lowered
    assert "instr(lower(v_condition), '''groq''') = 0" in lowered
    # 约束不存在（V001 全新建库已带新集合 / 表还没建）→ 跳过而不是报错。
    assert "when no_data_found then" in lowered

    # 历史迁移冻结：V026 不许提前出现新 kind，否则「存量库走 V027」这条路径
    # 会在 V026 上被绕过，V027 的幂等分支也失去意义。
    v026 = _ocr_text().lower()
    assert "'baidu'" not in v026
    assert "'groq'" not in v026
    assert LEGACY_OCR_KIND_CK in _ocr()["tables"]["ai_ocr_engines"]["ck"]


def test_v029_extends_kind_with_zhipu() -> None:
    """V029 把 kind 从五值扩到六值（+zhipu），与 models/V001 逐字一致。

    与 V027 同一套断言口径：约束名与表达式逐字一致、幂等是先读后改、历史迁移
    V027 保持冻结。
    """
    text = _zhipu_kind_text()
    extracted = ddl_check.extract_execute_immediate(text)

    added = re.search(
        r"ALTER TABLE\s+ai_ocr_engines\s+ADD CONSTRAINT\s+(\w+)\s+CHECK\s*\((.*)\)",
        extracted,
        re.IGNORECASE | re.DOTALL,
    )
    assert added, extracted
    assert added.group(1).lower() == OCR_KIND_CONSTRAINT
    parsed = ddl_check._parse_constraint(
        f"constraint {added.group(1)} check ({added.group(2)})"
    )
    assert parsed == ("ck", OCR_KIND_CK)

    modeled_ck = ddl_check.parse_models()["tables"]["ai_ocr_engines"]["ck"]
    v001_ck = ddl_check.parse_v001()["tables"]["ai_ocr_engines"]["ck"]
    assert parsed[1] in modeled_ck
    assert parsed[1] in v001_ck
    assert "'zhipu'" in parsed[1]

    lowered = text.lower()
    read_at = lowered.index("from user_constraints")
    needs_change_at = lowered.index("if v_needs_change then")
    drop_at = lowered.index("drop constraint ck_ai_ocr_engines_kind")
    add_at = lowered.index("add constraint ck_ai_ocr_engines_kind")
    assert read_at < needs_change_at < drop_at < add_at
    assert "instr(lower(v_condition), '''zhipu''') = 0" in lowered
    assert "when no_data_found then" in lowered

    # 历史迁移冻结：V027 不许提前出现 zhipu，否则「存量库走 V029」这条路径会被
    # V027 绕过，V029 的幂等分支也就失去意义。
    assert "'zhipu'" not in _ocr_kind_text().lower()


def test_credentials_migration_adds_exactly_the_modeled_key_scope() -> None:
    """V025 的 ALTER 必须补上与 models/V001 完全相同的列与约束。"""
    import re

    extracted = ddl_check.extract_execute_immediate(_credentials_text())
    modeled = ddl_check.parse_models()
    expected_column = modeled["tables"]["ai_call_logs"]["columns"]["key_scope"]

    # 贪婪匹配到行尾：列类型里自己带括号（VARCHAR2(16)），非贪婪会把类型截断。
    column_match = re.search(
        r"ALTER TABLE ai_call_logs ADD \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert column_match, extracted
    name, type_, notnull, default = ddl_check._parse_column(column_match.group(1))
    assert name == "key_scope"
    assert (type_, notnull, default) == expected_column

    constraint_match = re.search(
        r"ALTER TABLE ai_call_logs ADD CONSTRAINT (\w+) CHECK \((.*)\)",
        extracted,
        re.IGNORECASE | re.DOTALL,
    )
    assert constraint_match, extracted
    assert constraint_match.group(1).lower() == "ck_ai_call_logs_key_scope"
    parsed = ddl_check._parse_constraint(
        f"constraint {constraint_match.group(1)} check ({constraint_match.group(2)})"
    )
    assert parsed == ("ck", KEY_SCOPE_CK)
    assert parsed[1] in modeled["tables"]["ai_call_logs"]["ck"]


def test_gateway_migration_is_idempotent_on_existing_objects() -> None:
    text = _gateway_text()
    # 已存在表 -> ORA-00955；索引重名/重复列 -> ORA-01408；其余错误必须继续抛。
    assert "-955" in text
    assert "-1408" in text
    assert "EXECUTE IMMEDIATE" in text


def test_credentials_migration_is_idempotent_on_existing_objects() -> None:
    text = _credentials_text()
    assert "-955" in text
    assert "-1408" in text
    # ALTER TABLE ADD 列已存在 -> ORA-01430：存量库重复执行不能炸在加列上。
    assert "-1430" in text
    assert "EXECUTE IMMEDIATE" in text


def test_ocr_migration_is_idempotent_on_existing_objects() -> None:
    text = _ocr_text()
    assert "EXECUTE IMMEDIATE" in text
    # 表/索引重名 -> -955；索引已存在 -> -1408；列已存在 -> -1430；
    # 约束名已占用 -> -2264；约束不存在（DROP 重跑）-> -2443。
    for code in ("-955", "-1408", "-1430", "-2264", "-2443"):
        assert code in text


def test_v028_adds_exactly_the_modeled_expiry_column() -> None:
    """V028 只做一件事：给 ai_credentials 补 ``expires_at``（与 models/V001 同型）。

    三件事都必须钉住：列定义逐字段一致（类型 / NOT NULL / 默认值）、幂等是
    「吞 ORA-01430/-955」、以及历史迁移 V025/V026 保持冻结（不得提前出现该列）。
    """
    extracted = ddl_check.extract_execute_immediate(_expiry_text())
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    added = re.findall(
        r"ALTER TABLE ai_credentials ADD \((.*)\)\s*$",
        extracted,
        re.IGNORECASE | re.MULTILINE,
    )
    assert added, extracted
    # 只加一列：多出来的列说明这次迁移夹带了别的东西。
    assert [frag.split()[0].lower() for frag in added] == [EXPIRY_COLUMN]

    name, type_, notnull, default = ddl_check._parse_column(added[0])
    assert name == EXPIRY_COLUMN
    assert notnull is False  # NULL = 永不过期
    assert default is None
    assert (type_, notnull, default) == modeled["tables"]["ai_credentials"]["columns"][
        EXPIRY_COLUMN
    ]
    assert v001["tables"]["ai_credentials"]["columns"][EXPIRY_COLUMN] == (
        type_,
        notnull,
        default,
    )

    # 幂等：列已存在（-1430）/ 表已由更新基线带上该列（-955）都收敛。
    text = _expiry_text()
    assert "EXECUTE IMMEDIATE" in text
    assert "-1430" in text
    assert "-955" in text

    # 历史迁移冻结：V025/V026 不许提前出现 expires_at。
    assert EXPIRY_COLUMN not in _credentials_text().lower()
    assert EXPIRY_COLUMN not in _ocr_text().lower()


def test_ai_tables_have_no_foreign_keys() -> None:
    """审计与凭据都要活得比账号久：四张表在 models/V001/迁移里都不许有外键。"""
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()
    gateway = _gateway()
    credentials = _credentials()
    ocr = _ocr()
    api_keys = _api_keys()

    for table in ("ai_call_logs", "ai_quota"):
        assert modeled["tables"][table]["fk"] == set()
        assert v001["tables"][table]["fk"] == set()
        assert gateway["tables"][table]["fk"] == set()

    assert modeled["tables"]["ai_credentials"]["fk"] == set()
    assert v001["tables"]["ai_credentials"]["fk"] == set()
    assert credentials["tables"]["ai_credentials"]["fk"] == set()

    assert modeled["tables"]["ai_ocr_engines"]["fk"] == set()
    assert v001["tables"]["ai_ocr_engines"]["fk"] == set()
    assert ocr["tables"]["ai_ocr_engines"]["fk"] == set()

    assert modeled["tables"]["ai_api_keys"]["fk"] == set()
    assert v001["tables"]["ai_api_keys"]["fk"] == set()
    assert api_keys["tables"]["ai_api_keys"]["fk"] == set()


def test_api_keys_migration_matches_models_and_v001() -> None:
    """V030 建的表必须与 models / V001 逐字段一致（含列默认值与唯一索引）。"""
    api_keys = _api_keys()
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    expected = modeled["tables"]["ai_api_keys"]
    assert api_keys["tables"]["ai_api_keys"] == expected
    assert v001["tables"]["ai_api_keys"] == expected

    indexes = {
        ("ai_api_keys", True, ("token_hash",)),
        ("ai_api_keys", True, ("user_oid", "name")),
    }
    assert indexes <= api_keys["indexes"]
    assert indexes <= modeled["indexes"]
    assert indexes <= v001["indexes"]


def test_api_keys_columns_carry_the_documented_shape() -> None:
    """列的「形状」是这次迁移的实质内容：NOT NULL / 默认值 / 可空都在这里钉住。"""
    columns = ddl_check.parse_models()["tables"]["ai_api_keys"]["columns"]

    assert columns["user_oid"] == ("varchar2(24)", True, None)
    assert columns["name"] == ("varchar2(64)", True, None)
    # sha256 的 hex 恒为 64 字符；存不下就是写不进去，不是截断。
    assert columns["token_hash"] == ("varchar2(64)", True, None)
    assert columns["key_hint"] == ("varchar2(16)", True, None)
    assert columns["enabled"] == ("number(1)", True, "1")
    # NULL = 永不过期 / 从未用过。
    assert columns["expires_at"] == ("timestamp", False, None)
    assert columns["last_used_at"] == ("timestamp", False, None)
    assert columns["created_at"] == ("timestamp", True, "SYSTIMESTAMP")


def test_api_keys_migration_is_idempotent_on_existing_objects() -> None:
    """重跑不能炸：表已存在 → -955，索引已存在 → -1408。"""
    text = _api_keys_text()

    assert "EXECUTE IMMEDIATE" in text
    for code in ("-955", "-1408"):
        assert code in text

