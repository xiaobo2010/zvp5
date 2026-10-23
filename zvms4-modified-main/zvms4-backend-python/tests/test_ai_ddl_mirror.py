"""AI 网关迁移的三方镜像：V024 / V025 / V026 / ``db/models.py`` / V001，离线不连库。

``db.ddl_check`` 只比对 models 与 V001（``python -m db.ddl_check``），所以「V024、
V025、V026 是否真的与 models 一致」这条事实没有任何东西守着——它一漂，存量实例
（走 V024+V025+V026）与全新实例（走 V001）就会长出不同的 AI 表。这里用
ddl_check 的解析器把这些迁移文件也拉进同一次比较。

迁移是历史事实：V024/V025 描述的是当时的形状，后续迁移只做增量。所以下面的
断言写成「V024/V025 == models **减去** V026 的增量」，而不是让历史文件去追新
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
# ai_call_logs 补 key_scope，V026 建 OCR 引擎表并放宽 ai_quota.kind。
GATEWAY_FILE = "V024__add_ai_gateway.sql"
CREDENTIALS_FILE = "V025__add_ai_credentials.sql"
OCR_FILE = "V026__add_ai_ocr_engines_and_key_headers.sql"

# ``ai_call_logs.key_scope`` 的 CHECK 规范化后的形态（ddl_check 会把大小写与空白
# 折叠掉）。V024 建表时还没有这一列，它由 V025 的幂等 ALTER 补上——所以下面既断言
# 「models/V001 里有它」，也断言「V024 里没有它」，把这次拆分本身钉住。
KEY_SCOPE_CK = "key_scope is null or key_scope in ('personal','shared','server')"

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


def _credentials_text() -> str:
    return _migration(CREDENTIALS_FILE).read_text(encoding="utf-8")


def _ocr() -> dict:
    return _parsed(_migration(OCR_FILE))


def _ocr_text() -> str:
    return _migration(OCR_FILE).read_text(encoding="utf-8")


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
    # V024 建表时还没有 key_scope：把它从 models 侧摘掉后必须与迁移逐字段相等。
    without_key_scope = {
        "columns": {k: v for k, v in expected["columns"].items() if k != "key_scope"},
        "pk": expected["pk"],
        "uq": set(expected["uq"]),
        "fk": set(expected["fk"]),
        "ck": {ck for ck in expected["ck"] if ck != KEY_SCOPE_CK},
    }
    assert gateway["tables"]["ai_call_logs"] == without_key_scope

    # 全新实例走 V001，一次就拿到完整形状（含 key_scope 与它的 CHECK）。
    assert v001["tables"]["ai_call_logs"] == expected
    assert "key_scope" in v001["tables"]["ai_call_logs"]["columns"]
    assert KEY_SCOPE_CK in v001["tables"]["ai_call_logs"]["ck"]


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
    # V025 建表时还没有请求形状列；V026 补齐（见下一个测试）。
    before_headers = {
        "columns": {
            k: v for k, v in expected["columns"].items() if k not in HEADER_COLUMNS
        },
        "pk": expected["pk"],
        "uq": set(expected["uq"]),
        "fk": set(expected["fk"]),
        "ck": expected["ck"] - {HEADERS_JSON_CK},
    }
    assert credentials["tables"]["ai_credentials"] == before_headers

    # 全新实例走 V001，一次就拿到完整形状（含 headers_json / api_path）。
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
    assert ocr["tables"]["ai_ocr_engines"] == expected
    assert v001["tables"]["ai_ocr_engines"] == expected

    indexes = {
        ("ai_ocr_engines", True, ("name",)),
        ("ai_ocr_engines", False, ("enabled", "priority")),
    }
    assert indexes <= ocr["indexes"]
    assert indexes <= modeled["indexes"]
    assert indexes <= v001["indexes"]


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


def test_ai_tables_have_no_foreign_keys() -> None:
    """审计与凭据都要活得比账号久：四张表在 models/V001/迁移里都不许有外键。"""
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()
    gateway = _gateway()
    credentials = _credentials()
    ocr = _ocr()

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
