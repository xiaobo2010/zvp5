"""AI 网关数据层的离线 SQL 契约（不连库、不建引擎）。

这三条语句各有一次「写错了也不会报错、只会在生产上静默出错」的机会：

* ``ai_quota._consume_stmt`` 必须是**一条**带 ``used + :amount <= limit_count``
  谓词的条件 UPDATE。改成「先 SELECT used、在 Python 里判断、再 UPDATE」照样能过
  单测，但并发下会超卖——所以这里断言 SQL 形状本身（含「不含 SELECT」）。
* ``ai_credentials._list_visible_stmt`` 的排序必须是 ``CASE``：``ORDER BY scope``
  是按字符串比较，``'shared' > 'personal'`` 会把共享项顶到个人项前面，遮蔽规则
  随之失效。
* ``ai_call_logs._insert_stmt`` 必须显式绑定 ``key_scope``（含 ``None``）：
  「不知道」与「平台出资 server」是两件事，靠列默认值表达不了。

编译时把绑定值内联（``literal_binds``），断言的是实际会发给 Oracle 的文本。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects import oracle

from db.repos.oracle import ai_call_logs as ai_call_logs_repo
from db.repos.oracle import ai_api_keys as ai_api_keys_repo
from db.repos.oracle import ai_credentials as ai_credentials_repo
from db.repos.oracle import ai_quota as ai_quota_repo

DIALECT = oracle.dialect()
USER_OID = "000000000000000000000002"
PERIOD = "202610"
TS = datetime(2026, 10, 23, 1, 2, 3)


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


def _consume(amount: int = 1) -> str:
    return _sql(
        ai_quota_repo._consume_stmt(USER_OID, PERIOD, "chat", amount, TS)
    )


# ---------------------------------------------------------------------------
# AI_QUOTA：单语句条件扣减
# ---------------------------------------------------------------------------


def test_consume_is_a_single_conditional_update() -> None:
    sql = _consume()

    assert sql.upper().startswith("UPDATE AI_QUOTA SET ")
    # 自增在 SET 里、闸门在 WHERE 里：两者同一条语句才由行锁串行化。
    assert "SET used=(ai_quota.used + 1)" in sql
    assert "AND ai_quota.used + 1 <= ai_quota.limit_count" in sql
    # 三个键谓词缺一个都会扣错行（别人的额度 / 上个月的额度 / 另一种能力）。
    assert f"ai_quota.user_oid = '{USER_OID}'" in sql
    assert f"ai_quota.period = '{PERIOD}'" in sql
    assert "ai_quota.kind = 'chat'" in sql


def test_consume_does_not_read_before_write() -> None:
    """单语句 = 没有 SELECT、没有 DELETE；读后写会让并发扣减超卖。"""
    sql = _consume().upper()
    assert "SELECT" not in sql
    assert "DELETE" not in sql
    assert sql.count("UPDATE AI_QUOTA") == 1
    assert ";" not in sql


def test_consume_binds_the_amount_into_both_sides() -> None:
    """amount 必须同时进 SET 与 WHERE：只改一边就会「扣 3 记 1」或反之。"""
    sql = _consume(amount=3)
    assert "SET used=(ai_quota.used + 3)" in sql
    assert "AND ai_quota.used + 3 <= ai_quota.limit_count" in sql


def test_set_limit_never_resets_used() -> None:
    """管理员调上限不该把已用额度清零：SET 里只能有 limit_count / updated_at。"""
    sql = _sql(
        ai_quota_repo._update_limit_stmt(USER_OID, PERIOD, "ocr", 500, TS)
    )
    assert "SET limit_count=500, updated_at=" in sql
    assert "used" not in sql.split("SET ", 1)[1]


# ---------------------------------------------------------------------------
# AI_CREDENTIALS：可见性 + 遮蔽顺序
# ---------------------------------------------------------------------------


def test_list_visible_orders_personal_first_with_case() -> None:
    sql = _sql(ai_credentials_repo._list_visible_stmt(USER_OID))

    assert (
        "ORDER BY CASE ai_credentials.scope WHEN 'personal' THEN 0 ELSE 1 END"
        in sql
    )
    # 按字符串排会得到相反的结果（'shared' > 'personal'），这里把错误写法钉死。
    assert "scope DESC" not in sql
    assert sql.index("CASE ai_credentials.scope") < sql.index(
        "ai_credentials.name ASC"
    )


def test_list_visible_scope_predicate_is_own_personal_or_any_shared() -> None:
    sql = _sql(ai_credentials_repo._list_visible_stmt(USER_OID))

    assert f"ai_credentials.owner_oid = '{USER_OID}'" in sql
    assert "ai_credentials.scope = 'shared'" in sql
    assert " OR " in sql.split(" WHERE ", 1)[1]
    # disabled 行要一起取回来（过滤是服务层的事），否则管理员看不到自己停用的 key。
    where = sql.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
    assert "enabled" not in where


def test_list_visible_projects_the_cipher_column() -> None:
    """服务层要解密，所以投影必须带 api_key_cipher（但绝不带明文列）。"""
    sql = _sql(ai_credentials_repo._list_visible_stmt(USER_OID))
    assert "ai_credentials.api_key_cipher" in sql
    assert "api_key," not in sql


# ---------------------------------------------------------------------------
# AI_CALL_LOGS：审计插入
# ---------------------------------------------------------------------------


def _insert(key_scope: str | None, **overrides) -> str:
    params = dict(
        oid="a" * 24,
        user_oid=USER_OID,
        capability="chat",
        endpoint="/api/ai/v1/chat/completions",
        provider="lab",
        model="gpt-x",
        status="success",
        http_status=200,
        error_code=None,
        latency_ms=12,
        units=1,
        input_bytes=None,
        request_id="req-1",
        key_scope=key_scope,
        created_at=TS,
    )
    params.update(overrides)
    return _sql(ai_call_logs_repo._insert_stmt(**params))


def test_insert_binds_key_scope_column() -> None:
    sql = _insert("personal")

    assert "request_id, key_scope, prompt_tokens" in sql
    # key_scope 紧邻 request_id 之后、V031 的 token 列之前：锚定这个位置就能
    # 确定它落在 key_scope 那一格，而不是碰巧在别处出现了同样的字符串。
    assert "'req-1', 'personal', NULL, NULL, NULL" in sql


def test_insert_binds_null_key_scope_explicitly() -> None:
    """``None`` 必须写成 NULL，而不是让列默认值顶替（默认值表达不了「未知」）。"""
    sql = _insert(None)
    assert "'req-1', NULL, NULL, NULL, NULL" in sql
    assert "'req-1', 'server'" not in sql


def test_insert_binds_the_trace_columns_explicitly() -> None:
    """V031 的元数据列没有列默认值：``None`` 必须写成 NULL（不是 0）。"""
    sql = _insert(
        "shared",
        prompt_tokens=11,
        completion_tokens=22,
        total_tokens=33,
        attempt_no=2,
        purpose="chat",
        first_token_ms=140,
        engine_kind=None,
        attachment_oid=None,
    )

    assert (
        "11, 22, 33, 2, 'chat', 140, NULL, NULL, TO_DATE(" in sql
    ), sql
    # NULL 不是 0：三个 token 列没值时写的是 NULL，读侧才能判「这是估算」。
    bare = _insert("shared")
    assert "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, TO_DATE(" in bare


def test_insert_is_one_statement() -> None:
    sql = _insert("shared").upper()
    assert sql.startswith("INSERT INTO AI_CALL_LOGS ")
    assert sql.count("INSERT") == 1
    assert "SELECT" not in sql
    assert "DELETE" not in sql


# ---------------------------------------------------------------------------
# AI_CALL_LOGS：管理端日志查询的过滤谓词
# ---------------------------------------------------------------------------
#
# 这几个谓词各有一个静默出错的形态：``key_scope`` 漏了 = 按「哪类 key 付的钱」
# 筛选永远返回全量；时间边界写成 ``>``/``<`` = 按秒排查时端点那一秒的调用消失；
# ``request_id`` 写成 LIKE = 索引让给全表扫且把子串当命中。
# 所以断言的是编译后的 SQL 文本（含 ``>=`` / ``<=`` 与 ``OR`` 的缺席）。


def _page(**kwargs) -> str:
    filters = {
        key: value
        for key, value in kwargs.items()
        if key in ai_call_logs_repo._FILTER_KEYS
    }
    page = int(kwargs.get("page", 1))
    size = int(kwargs.get("size", 20))
    conds = ai_call_logs_repo._filters(**filters)
    return _sql(ai_call_logs_repo._page_stmt(conds, page, size))


def test_page_stmt_filters_key_scope_by_equality() -> None:
    sql = _page(key_scope="personal")
    assert "ai_call_logs.key_scope = 'personal'" in sql


def test_page_stmt_time_bounds_are_inclusive() -> None:
    sql = _page(time_start=TS, time_end=TS)
    # 闭区间：``>=`` / ``<=``，不是 ``>`` / ``<``。
    assert "ai_call_logs.created_at >= TO_DATE('2026-10-23 01:02:03'" in sql
    assert "ai_call_logs.created_at <= TO_DATE('2026-10-23 01:02:03'" in sql


def test_page_stmt_omits_absent_filters() -> None:
    sql = _page()
    # key_scope 是投影列，所以只在 WHERE 里判缺席。
    assert " WHERE " not in sql
    assert "created_at >=" not in sql
    assert "created_at <=" not in sql


def test_page_stmt_filters_the_trace_columns_by_equality() -> None:
    """request_id / provider / model / purpose 都是精确匹配，走等号不等 LIKE。"""
    sql = _page(
        request_id="req-1",
        provider="lab",
        model="gpt-x",
        purpose="chat",
    )

    assert "ai_call_logs.request_id = 'req-1'" in sql
    assert "ai_call_logs.provider = 'lab'" in sql
    assert "ai_call_logs.model = 'gpt-x'" in sql
    assert "ai_call_logs.purpose = 'chat'" in sql
    assert " LIKE " not in sql.upper()


def test_total_stmt_shares_the_page_filters() -> None:
    conds = ai_call_logs_repo._filters(
        capability="ocr",
        status="timeout",
        key_scope="server",
        purpose="ocr",
        time_start=TS,
        time_end=TS,
    )
    sql = _sql(ai_call_logs_repo._total_stmt(conds))
    assert sql.upper().startswith("SELECT COUNT(*)")
    assert "ai_call_logs.capability = 'ocr'" in sql
    assert "ai_call_logs.status = 'timeout'" in sql
    assert "ai_call_logs.key_scope = 'server'" in sql
    assert "ai_call_logs.purpose = 'ocr'" in sql
    assert "ai_call_logs.created_at >= TO_DATE('2026-10-23 01:02:03'" in sql
    assert "ai_call_logs.created_at <= TO_DATE('2026-10-23 01:02:03'" in sql


# ---------------------------------------------------------------------------
# AI_CALL_LOGS：聚合（/api/ai/logs/stats）
# ---------------------------------------------------------------------------
#
# 统计面板最容易静默出错的两点：①「错误数」写成 ``status = 'error'``（漏掉
# rejected/busy，运营会以为拦截没发生）；② 按日分组用 ``CREATED_AT`` 原值而不是
# ``TRUNC``，每天一个数字变成每毫秒一个数字。


def test_stats_daily_counts_every_non_success_status_as_error() -> None:
    sql = _sql(ai_call_logs_repo._stats_daily_stmt([]))

    assert "trunc(ai_call_logs.created_at)" in sql
    assert "ai_call_logs.status != 'success'" in sql
    assert "GROUP BY trunc(ai_call_logs.created_at)" in sql


def test_stats_daily_sums_tokens_and_averages_latency() -> None:
    sql = _sql(ai_call_logs_repo._stats_daily_stmt([]))

    assert "sum(ai_call_logs.prompt_tokens)" in sql
    assert "sum(ai_call_logs.completion_tokens)" in sql
    assert "sum(ai_call_logs.total_tokens)" in sql
    assert "avg(ai_call_logs.latency_ms)" in sql


def test_stats_group_stmt_groups_by_the_given_dimension() -> None:
    from db.models import ai_call_logs as tbl

    sql = _sql(ai_call_logs_repo._stats_group_stmt(tbl.c.purpose, []))

    assert "ai_call_logs.purpose" in sql
    assert "GROUP BY ai_call_logs.purpose" in sql
    assert "sum(ai_call_logs.units)" in sql
    # 调用多的在前：面板第一眼要看到最烧钱/最常用的那个维度。
    assert "ORDER BY count(*) DESC" in sql


def test_stats_group_stmt_is_bounded() -> None:
    """分组小计必须有上限：purpose 是开放集合，model 会随供应商改版膨胀。"""
    from db.models import ai_call_logs as tbl

    sql = _sql(ai_call_logs_repo._stats_group_stmt(tbl.c.model, []))

    assert f"FETCH FIRST {ai_call_logs_repo.STATS_GROUP_LIMIT} ROWS ONLY" in sql


# ---------------------------------------------------------------------------
# AI_CREDENTIALS：expires_at 的三态
# ---------------------------------------------------------------------------
#
# ``None`` 在本协议里通常表示「不改」，所以「清除到期」必须靠 UNSET 哨兵与
# ``None`` 区分。写成「None 也当不改」不会报错，只会让管理员的「清除到期」
# 静默失效（key 到期后依旧被目录排除，他以为操作没生效）。


def test_update_values_omits_expires_at_when_unset() -> None:
    from db.repos.protocols import UNSET

    assert "expires_at" not in ai_credentials_repo._update_values(name="x")
    assert ai_credentials_repo._update_values(expires_at=UNSET) == {}


def test_update_values_none_clears_expires_at() -> None:
    assert ai_credentials_repo._update_values(expires_at=None) == {"expires_at": None}


def test_update_values_sets_the_expiry() -> None:
    assert ai_credentials_repo._update_values(expires_at=TS) == {"expires_at": TS}


def test_credentials_insert_binds_expires_at() -> None:
    sql = _sql(
        ai_credentials_repo._insert_stmt(
            oid="a" * 24,
            scope="shared",
            owner_oid="0" * 24,
            name="team",
            kind="openai_compat",
            base_url="https://api.example.com/v1",
            models=["m"],
            api_key_cipher="cipher",
            key_hint=None,
            enabled=True,
            created_at=TS,
            updated_at=TS,
            expires_at=None,
        )
    )
    # NULL 显式绑定：它表示「永不过期」这个事实，不是「没传」。
    assert "updated_at, expires_at)" in sql
    assert "TO_DATE('2026-10-23 01:02:03', 'YYYY-MM-DD HH24:MI:SS'), NULL)" in sql


# ---------------------------------------------------------------------------
# AI_API_KEYS：认证查询与 expires_at 的三态
# ---------------------------------------------------------------------------
#
# 认证是每个请求都要跑的路径，所以两件事必须在 SQL 形状里钉住：
#
# * 查 key 只按 ``token_hash`` 等值命中唯一索引（``ux_ai_api_keys_token_hash``）；
#   退化成「把行都取回来在 Python 里比」既慢，又会在拼错时静默放行。
# * ``update(expires_at=UNSET)`` 不写列，``None`` 写 NULL（永不过期），
#   ``datetime`` 写值——三种意图三次编译结果必须不同。


def _hash_sql() -> str:
    return _sql(ai_api_keys_repo._select_by_hash_stmt("a" * 64))


def test_api_key_lookup_is_an_equality_on_the_unique_index() -> None:
    sql = _hash_sql()

    assert sql.upper().startswith("SELECT ")
    assert f"ai_api_keys.token_hash = '{'a' * 64}'" in sql
    # 只有一个谓词：多一个「顺便按用户过滤」会让认证路径变成猜谜。
    assert sql.count("ai_api_keys.") >= 1
    assert " OR " not in sql.upper()
    assert " LIKE " not in sql.upper()


def test_api_key_lookup_never_projects_the_plaintext() -> None:
    """行形状里只有 ``token_hash``：没有 token 列可以投影。"""
    sql = _hash_sql().lower()
    assert "token_hash" in sql
    assert " token," not in sql
    assert " token " not in sql


def test_api_key_list_is_scoped_to_one_owner() -> None:
    sql = _sql(ai_api_keys_repo._list_stmt(USER_OID))

    assert f"ai_api_keys.user_oid = '{USER_OID}'" in sql
    # 新的在前：管理面板第一眼看到的是刚签发的 key。
    assert "ORDER BY ai_api_keys.created_at DESC" in sql


def test_api_key_count_is_scoped_to_one_owner() -> None:
    sql = _sql(ai_api_keys_repo._count_stmt(USER_OID))

    assert sql.upper().startswith("SELECT COUNT(*)")
    assert f"ai_api_keys.user_oid = '{USER_OID}'" in sql


def test_api_key_update_values_omit_unset_expiry() -> None:
    from db.repos.protocols import UNSET

    assert "expires_at" not in ai_api_keys_repo._update_values(name="ci")
    assert ai_api_keys_repo._update_values(expires_at=UNSET) == {}


def test_api_key_update_values_none_clears_expiry() -> None:
    assert ai_api_keys_repo._update_values(expires_at=None) == {"expires_at": None}


def test_api_key_update_values_sets_the_expiry_and_enabled_flag() -> None:
    assert ai_api_keys_repo._update_values(enabled=False) == {"enabled": 0}
    assert ai_api_keys_repo._update_values(expires_at=TS) == {"expires_at": TS}


def test_api_key_insert_binds_every_non_default_column() -> None:
    sql = _sql(
        ai_api_keys_repo._insert_stmt(
            oid="a" * 24,
            user_oid=USER_OID,
            name="ci",
            token_hash="b" * 64,
            key_hint="zvk_1234wxyz",
            enabled=True,
            expires_at=None,
            created_at=TS,
        )
    )

    assert sql.upper().startswith("INSERT INTO AI_API_KEYS ")
    assert "'ci'" in sql
    assert "'" + "b" * 64 + "'" in sql
    # created_at 显式绑定：列默认值的 SYSTIMESTAMP 在仓储层拿不到回读值。
    assert "TO_DATE('2026-10-23 01:02:03'" in sql
    assert "last_used_at" in sql


def test_api_key_touch_only_writes_last_used_at() -> None:
    sql = _sql(ai_api_keys_repo._touch_stmt("a" * 24, TS)).lower()

    assert sql.startswith("update ai_api_keys set last_used_at=")
    # 采样写不能顺手把 enabled / name / token_hash 改回去。
    assert "enabled" not in sql
    assert "name" not in sql
    assert "token_hash" not in sql
