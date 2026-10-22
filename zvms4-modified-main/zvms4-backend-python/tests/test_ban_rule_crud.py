"""规则节点的写路径（``OracleBanRulesRepo`` 的增删改查 + DDL 口径）离线契约。

为什么单独一个文件：规则的写路径是**管理端唯一能扩大封禁口径**的入口，写错
一个字段（漏 ``trigger``、两列时间不同源、删了内置规则）都不会报错，只会在
几天后表现成「策略突然不生效」。这里把四件事钉住：

* ``insert`` 返回 24 位 hex ``oid``，``created_at`` 与 ``updated_at`` 同源；
* ``delete_by_code`` 返回 rowcount（``0`` ⇒ 规则不存在，调用方转 404）；
* ``get_by_trigger`` 只按 trigger 过滤（求值入口的唯一键）；
* ``update_by_code`` 能改名，且不会把没传的列写成 NULL。

DDL 侧钉住 V016 的补齐动作与「旧的多设备规则不再被种子塞回来」。

全部离线：会话是假对象，语句只编译不执行。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import oracle

from db import ddl_check
from db import models
from db.repos import protocols
from db.repos.oracle.ban_rules import OracleBanRulesRepo

DIALECT = oracle.dialect()
DDL_DIR = Path(__file__).resolve().parents[1] / "db" / "oracle" / "ddl"
V001_PATH = DDL_DIR / "V001__initial_schema.sql"
V016_PATH = DDL_DIR / "V016__rule_engine_and_violations.sql"
V019_PATH = DDL_DIR / "V019__harden_ban_engine.sql"

NOW = datetime(2026, 10, 22, 9, 0, 0)


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


class _RowProxy:
    """模拟 SQLAlchemy ``Row``：实现只吃 ``row._mapping``。"""

    def __init__(self, mapping: dict | None) -> None:
        self._mapping = mapping


class FakeSession:
    """记录语句的假会话：``execute`` 返回可控的 rowcount / row。"""

    def __init__(self, *, row=None, rowcount: int = 1) -> None:
        self.statements: list = []
        self.commits = 0
        self._row = row
        self._rowcount = rowcount

    async def execute(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(
            rowcount=self._rowcount,
            first=lambda: (None if self._row is None else _RowProxy(self._row)),
            all=lambda: [] if self._row is None else [self._row],
        )

    async def commit(self) -> None:
        self.commits += 1


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def test_insert_returns_hex_oid_and_binds_every_column():
    session = FakeSession()
    repo = OracleBanRulesRepo(session=session)

    oid = _run(
        repo.insert(
            "my_rule",
            "我的规则",
            "platform_misuse",
            enabled=True,
            config='{"threshold":3,"window_hours":24}',
            penalties="[1,3,7]",
        )
    )

    assert re.fullmatch(r"[0-9a-f]{24}", oid)
    sql = _sql(session.statements[0])
    assert sql.startswith("INSERT INTO ban_rules ")
    for column in ("oid", "code", "name", "trigger", "enabled", "config",
                   "penalties", "builtin", "created_at", "updated_at"):
        assert column in sql
    assert "'my_rule'" in sql
    assert "'platform_misuse'" in sql
    # 运行时新建的规则不是内置规则（INSERT 里就是 0，不是 1）
    values = dict(session.statements[0].compile(dialect=DIALECT).params)
    assert values["builtin"] == 0
    assert values["enabled"] == 1


def test_insert_uses_one_timestamp_for_both_time_columns():
    """``updated_at`` 不能比 ``created_at`` 晚：刚建的行「最后修改」等于创建。"""
    session = FakeSession()
    repo = OracleBanRulesRepo(session=session)

    _run(
        repo.insert(
            "my_rule",
            "我的规则",
            "platform_misuse",
            enabled=False,
            config="{}",
            penalties="[1]",
        )
    )

    params = dict(
        session.statements[0].compile(dialect=DIALECT).params
    )
    assert params["created_at"] == params["updated_at"]
    assert params["enabled"] == 0
    assert params["builtin"] == 0


def test_insert_leaves_commit_to_the_caller_when_session_is_injected():
    """注入会话 = 事务边界归调用方；这里替它 commit 会把外层的原子性撕开。"""
    session = FakeSession()
    repo = OracleBanRulesRepo(session=session)

    _run(
        repo.insert(
            "my_rule", "我的规则", "platform_misuse",
            enabled=True, config="{}", penalties="[1]",
        )
    )

    assert session.commits == 0


# ---------------------------------------------------------------------------
# delete_by_code
# ---------------------------------------------------------------------------


def test_delete_by_code_returns_rowcount():
    session = FakeSession(rowcount=1)
    repo = OracleBanRulesRepo(session=session)

    assert _run(repo.delete_by_code("my_rule")) == 1
    sql = _sql(session.statements[0])
    assert sql.startswith("DELETE FROM ban_rules")
    assert "WHERE ban_rules.code = 'my_rule'" in sql
    assert session.commits == 0


def test_delete_by_code_reports_zero_for_missing_rule():
    session = FakeSession(rowcount=0)
    assert _run(OracleBanRulesRepo(session=session).delete_by_code("nope")) == 0


# ---------------------------------------------------------------------------
# get_by_trigger / get_by_code
# ---------------------------------------------------------------------------


def _mapping(**overrides) -> dict:
    base = {
        "oid": "0b0000000000000000000002",
        "code": "foreign_device_login",
        "name": "非主学海设备登录",
        "trigger_code": "foreign_device_login",
        "enabled": 1,
        "config": '{"threshold":3}',
        "penalties": "[1,3,7,14,30,null]",
        "builtin": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return base


def test_get_by_trigger_filters_on_the_trigger_column():
    session = FakeSession(row=_mapping())
    row = _run(OracleBanRulesRepo(session=session).get_by_trigger("foreign_device_login"))

    assert row["trigger"] == "foreign_device_login"
    assert row["enabled"] is True
    assert row["builtin"] is True
    sql = _sql(session.statements[0])
    assert "WHERE ban_rules.trigger_code = 'foreign_device_login'" in sql
    # 谓词只能落在 trigger 上：求值入口不能退化成「按 code 找」，
    # 否则管理员一改名，入口就失联了
    assert "WHERE" in sql
    assert sql.split("WHERE", 1)[1].strip().startswith("ban_rules.trigger_code =")


def test_get_by_trigger_returns_none_when_absent():
    session = FakeSession(row=None)
    assert _run(OracleBanRulesRepo(session=session).get_by_trigger("nope")) is None


# ---------------------------------------------------------------------------
# update_by_code
# ---------------------------------------------------------------------------


def test_update_by_code_can_rename_without_touching_other_columns():
    session = FakeSession(rowcount=1)
    repo = OracleBanRulesRepo(session=session)

    assert _run(repo.update_by_code("my_rule", name="新名字")) == 1
    sql = _sql(session.statements[0])
    assert sql.startswith("UPDATE ban_rules SET ")
    assert "'新名字'" in sql
    assert "updated_at=" in sql
    for untouched in ("enabled=", "config=", "penalties=", "trigger="):
        assert untouched not in sql


# ---------------------------------------------------------------------------
# 协议与 schema
# ---------------------------------------------------------------------------


def test_ban_rules_repo_protocol_declares_the_new_methods():
    for method in ("get_by_trigger", "insert", "delete_by_code", "update_by_code"):
        assert hasattr(protocols.BanRulesRepo, method)


def test_violations_repo_protocol_shape():
    for method in ("insert", "exists_recent", "count_since", "list_page"):
        assert hasattr(protocols.ViolationsRepo, method)


def test_ban_rules_trigger_is_uniquely_indexed():
    """一个检查项一条规则：唯一索引是「求值结果可复现」的数据库保证。"""
    unique = [
        index
        for index in models.ban_rules.indexes
        if index.unique and [col.name for col in index.columns] == ["trigger_code"]
    ]
    assert unique, "ban_rules(trigger) 缺少唯一索引"


# ---------------------------------------------------------------------------
# DDL：V016 补齐 + 旧规则不再被种子塞回来
# ---------------------------------------------------------------------------


def _ddl(sql_path: Path) -> str:
    return ddl_check.extract_execute_immediate(sql_path.read_text(encoding="utf-8"))


def test_v015_adds_trigger_column_and_unique_index():
    ddl = _ddl(V016_PATH)
    assert "ALTER TABLE ban_rules ADD trigger_code VARCHAR2(64 CHAR)" in ddl
    assert "CREATE UNIQUE INDEX ux_ban_rules_trigger ON ban_rules (trigger_code)" in ddl


def test_v015_creates_violations_table_and_counting_index():
    ddl = _ddl(V016_PATH)
    assert "CREATE TABLE ban_violations" in ddl
    for column in ("user_oid VARCHAR2(24 CHAR) NOT NULL",
                   "trigger_code VARCHAR2(64 CHAR) NOT NULL",
                   "signal VARCHAR2(32 CHAR) NOT NULL",
                   "detail VARCHAR2(500 CHAR)"):
        assert column in ddl
    assert (
        "CREATE INDEX ix_ban_violations_user ON ban_violations "
        "(user_oid, trigger_code, created_at)" in ddl
    )


def test_v015_swallows_already_exists_errors():
    """幂等：重跑一次迁移必须什么都不炸。"""
    text = V016_PATH.read_text(encoding="utf-8")
    assert "IF SQLCODE != -1430 THEN RAISE; END IF;" in text  # 列已存在
    assert "IF SQLCODE != -955 THEN RAISE; END IF;" in text  # 表已存在
    assert "IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;" in text  # 索引已存在
    assert "IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;" in text  # 种子已插入


def test_v015_deletes_the_legacy_multi_device_rule():
    text = V016_PATH.read_text(encoding="utf-8")
    assert "DELETE FROM ban_rules WHERE code = 'multi_device_login'" in text


def test_neither_v001_nor_v015_seeds_the_legacy_rule():
    """旧规则只能被删，不能被任何种子重新插回来（否则迁移白做）。"""
    for path in (V001_PATH, V016_PATH):
        text = path.read_text(encoding="utf-8")
        seed = [
            line
            for line in text.splitlines()
            if "multi_device_login" in line
            and not line.strip().startswith("--")
            and "DELETE" not in line
        ]
        assert seed == [], f"{path.name} 仍在种子/更新 multi_device_login"


@pytest.mark.parametrize("path", [V001_PATH, V016_PATH])
def test_seeded_foreign_device_rule_matches_the_registry(path: Path) -> None:
    """种子行必须自带 trigger 和 {threshold} 配置，否则设备闸读不到上限。"""
    text = path.read_text(encoding="utf-8")
    assert "''foreign_device_login'', ''非主学海设备登录''" in text or (
        "'foreign_device_login', '非主学海设备登录'" in text
    )
    assert "trigger" in text
    assert '{"threshold":3}' in text or "''{\"threshold\":3}''" in text


# ---------------------------------------------------------------------------
# DDL：V019 加固（批次列 + 索引 + platform_misuse 种子）
# ---------------------------------------------------------------------------


def test_v019_adds_batch_id_and_its_index():
    ddl = _ddl(V019_PATH)

    assert "ALTER TABLE ban_violations ADD batch_id VARCHAR2(32 CHAR)" in ddl
    assert (
        "CREATE INDEX ix_ban_violations_batch ON ban_violations "
        "(user_oid, trigger_code, created_at, batch_id)" in ddl
    )


def test_v019_seeds_platform_misuse_rule():
    ddl = _ddl(V019_PATH)

    assert "MERGE INTO ban_rules" in ddl
    assert "'platform_misuse'" in ddl
    assert '"threshold":3,"window_hours":24' in ddl
    assert "[1,3,7]" in ddl
    # 种子必须带 trigger：上报闸按 trigger 取规则，没有它就等于「只记录不封」。
    assert "platform_misuse" in ddl.split("VALUES", 1)[1]


def test_v019_swallows_already_exists_errors():
    text = V019_PATH.read_text(encoding="utf-8")

    assert "IF SQLCODE != -1430 THEN RAISE; END IF;" in text
    assert "IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;" in text
    assert "IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;" in text


def test_v001_mirrors_v019_batch_column_index_and_seed():
    """新建实例走 V001：列/索引/种子必须与 V019 完全一致（ddl_check 只盯前两者）。"""
    ddl = _ddl(V001_PATH)

    assert "batch_id VARCHAR2(32 CHAR)" in ddl
    assert (
        "CREATE INDEX ix_ban_violations_batch ON ban_violations "
        "(user_oid, trigger_code, created_at, batch_id)" in ddl
    )
    assert "MERGE INTO ban_rules" in ddl
    assert "'0b0000000000000000000003'" in ddl
    assert '"threshold":3,"window_hours":24' in ddl
    assert "[1,3,7]" in ddl


# ---------------------------------------------------------------------------
# DDL：V020 风险规则种子（七条服务端可观测规则）
# ---------------------------------------------------------------------------

V020_PATH = DDL_DIR / "V020__risk_rules.sql"

#: trigger → (阈值, 窗口小时, mode)
RISK_SEEDS = {
    "login_failure_burst": (10, 1, "ban"),
    "ban_evasion_retry": (5, 1, "ban"),
    "challenge_flood": (8, 1, "alert"),
    "temp_password_issue_burst": (20, 1, "alert"),
    "export_burst": (10, 1, "alert"),
    "login_success_churn": (15, 1, "alert"),
    "device_switch_burst": (5, 24, "alert"),
}


@pytest.mark.parametrize("path", [V001_PATH, V020_PATH])
def test_risk_seeds_are_present_and_configured(path: Path) -> None:
    """种子必须与 ``services.risk_monitor.TRIGGERS`` 的默认值逐字一致。"""
    ddl = _ddl(path)

    for trigger, (threshold, hours, mode) in RISK_SEEDS.items():
        # ``_ddl`` 会把 SQL 里的 '' 还原成 '，所以这里比较的是真实的 SQL 文本。
        # 规则 code 与 trigger 同名：MERGE 的 ON 子句 + VALUES 里两列共三处。
        assert ddl.count(f"'{trigger}'") >= 3, f"{path.name} 缺 {trigger} 种子"
        # 102 行的 VALUES 顺序是 (oid, code, name, trigger_code, enabled, config)：
        # trigger_code 后面紧跟 enabled=1 与 config，缺 trigger 就等于「规则不生效」。
        assert (
            f"'{trigger}', 1, '{{\"threshold\":{threshold},"
            f'\"window_hours\":{hours},\"mode\":\"{mode}\"}}' in ddl
        ), f"{path.name} 的 {trigger} trigger/config 与默认值不一致"


@pytest.mark.parametrize("path", [V001_PATH, V020_PATH])
def test_risk_seeds_penalties_follow_the_mode(path: Path) -> None:
    """alert 模式的规则不封人：penalties 落 JSON null，ban 模式给梯度。"""
    ddl = _ddl(path)

    assert ddl.count("'null', 1, SYSTIMESTAMP, SYSTIMESTAMP") == 5
    assert ddl.count("'[1,3,7]', 1, SYSTIMESTAMP, SYSTIMESTAMP") >= 2


@pytest.mark.parametrize("path", [V001_PATH, V020_PATH])
def test_risk_seeds_are_idempotent_merges(path: Path) -> None:
    """幂等：WHEN NOT MATCHED 才插，重复执行不覆盖管理员改过的值。"""
    text = path.read_text(encoding="utf-8")

    assert "WHEN NOT MATCHED THEN INSERT" in text
    assert "WHEN MATCHED THEN UPDATE" not in text
    assert "IF SQLCODE NOT IN (-1, -955) THEN RAISE; END IF;" in text


def test_v020_has_no_ddl_changes():
    """本次扩展不改表结构：V020 里除了种子 MERGE 不该有 ALTER/CREATE。"""
    ddl = _ddl(V020_PATH)

    assert "MERGE INTO ban_rules" in ddl
    for forbidden in ("ALTER TABLE", "CREATE TABLE", "CREATE INDEX"):
        assert forbidden not in ddl


def test_risk_seed_defaults_match_the_service_registry() -> None:
    """DDL 与 ``services.risk_monitor.TRIGGERS`` 是同一份默认值，不许漂移。"""
    from services import risk_monitor

    assert set(risk_monitor.TRIGGERS) == set(RISK_SEEDS)
    for trigger, (threshold, hours, mode) in RISK_SEEDS.items():
        assert risk_monitor.TRIGGERS[trigger]["config"] == {
            "threshold": threshold,
            "window_hours": hours,
            "mode": mode,
        }


def test_risk_seed_defaults_match_the_router_registry() -> None:
    """管理端表单初值同样来自注册表：三处（DDL / 服务 / 注册表）必须一致。"""
    from routers import ban_router

    for trigger, (threshold, hours, mode) in RISK_SEEDS.items():
        entry = ban_router.TRIGGER_REGISTRY[trigger]
        assert entry["evaluation"] == "monitor"
        assert entry["config"] == {
            "threshold": threshold,
            "window_hours": hours,
            "mode": mode,
        }
