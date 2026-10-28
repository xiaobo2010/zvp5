"""数据库内容管理原语（Stage 2，契约 §4）。

本模块只做「标识符校验 + SQL 组装 + 行序列化」，不碰 FastAPI / 鉴权 / 审计；
路由层负责管理员闸、双人 TOTP 与审计。这样只读闸、标识符闸、LOB 序列化都能
脱离 HTTP 单独测试，也不必为了单测去连真库。

三条不可让步的边界：

* 标识符只用于**拼接**（先过正则、再查数据字典、最后大写加双引号），值一律走
  绑定参数。请求里的值永远不进 SQL 文本，注入面因此只剩「已存在的真实列名」。
* ``/query`` 是只读闸：多语句、写关键字、非 ``SELECT``/``WITH`` 开头一律拒绝。
  写入口只有带双人码的 ``/ddl``，读接口被绕过的代价太高（一条 UPDATE 就能改
  掉全表，而且不需要审批）。
* 分页靠 ``limit+1`` 判定 ``has_more``，不做 ``COUNT(*)``：ADB 上对大表做全表
  计数既慢又按扫描量计费，而前端只需要知道「还有没有下一页」。

Oracle 细节：源库是 19c（ADB），``OFFSET/FETCH`` 与绑定变量在行限制子句里都
支持；``user_tables`` / ``user_tab_columns`` 是当前 schema 的数据字典视图，
用它们做存在性校验比 ``ALL_TABLES`` 更严格（不会误认别的 schema 的表）。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, time as time_of_day, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from db.oracle.session import session_scope

# 契约 §4.1：行分页 ≤ 200，SQL 查询 ≤ 500。路由层的 Query(le=...) 已经拦一次，
# 服务层再夹一次是为了「非 HTTP 调用方」（脚本 / 未来的任务）也守同一条线。
MAX_ROW_LIMIT = 200
MAX_QUERY_LIMIT = 500

# 契约 §4.3.3：30s 超时。优先设置驱动的 call_timeout（服务端真能中断），
# 外层再套 asyncio.wait_for 兜底（替身会话 / 驱动不支持时也能释放请求）。
QUERY_TIMEOUT_SECONDS = 30

# 契约 §4.5：LOB 只回长度与前 2KB 预览。2048 字符是「够前端判断内容类型、
# 又不至于把 10MB 的 CLOB 塞进 JSON」的折中值。
LOB_PREVIEW_CHARS = 2048

# 契约 §4.2：1-30 位、字母开头。``$`` / ``#`` 是 Oracle 允许的标识符字符，
# 收紧到 30 位是因为 19c 之前的标识符上限是 30 字节，线上 DDL 都按这个约束写。
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,29}$")

# 只读闸的禁用词（契约 §4.3.2）。用词边界匹配：``CREATED_AT`` 不会被 ``CREATE``
# 误伤，``DELETE``/``DELETE_X`` 的边界也分得清。
_FORBIDDEN_WORDS: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "BEGIN",
    "DECLARE",
    "CALL",
    "EXECUTE",
    "COMMIT",
    "ROLLBACK",
)
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_WORDS) + r")\b")
_READONLY_VERBS = frozenset({"SELECT", "WITH"})

_DDL_VERBS = frozenset(
    {"DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE", "COMMENT", "RENAME"}
)
_DML_VERBS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})

# 契约 §4.4.2：这三张表是双人授权/回收站/变更日志的底座，改一条就可能让审计
# 失去可信度（例如删掉 dual_auth_uses 里的重放记录），所以任何写语句一律拒绝。
_PROTECTED_TABLES: tuple[str, ...] = ("DUAL_AUTH_USES", "USER_TRASH", "CHANGELOG")

# Tables whose *rows* carry credentials/audit substrate. The row reader is
# still useful for ordinary content tables, but these must go through a
# dedicated, dual-auth operation, not the generic console.
_PROTECTED_READ_TABLES: tuple[str, ...] = (
    "DUAL_AUTH_USES",
    "USER_TRASH",
    "TEMP_LOGIN_PASSWORDS",
    "DEVICE_CHALLENGES",
)

# Column values that must never be returned by the generic content console.
# `PAYLOAD` is the archived user row (password hash + session id included);
# `*_HASH` covers PASSWORD_HASH / CODE_HASH / TOKEN_HASH.
_SENSITIVE_COLUMNS: frozenset[str] = frozenset(
    {"PASSWORD_HASH", "SESSION_ID", "CODE_HASH", "TOKEN_HASH", "PAYLOAD"}
)

# 列名判定挡不住别名（``SELECT TOKEN_HASH AS x`` 的返回列名就是 ``x``），所以
# 再加一层**按值**兜底：形状本身就是凭据的值，无论在哪一列都脱敏。
# 刻意不收 24 位 hex：那是业务对象 oid（活动/成员/用户），脱敏了就不可用。
_CREDENTIAL_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # bcrypt / argon2 口令散列
    re.compile(r"^\$2[aby]\$"),
    re.compile(r"^\$argon2"),
    # JWT（header 恒以 eyJ 开头：{"alg":...
    re.compile(r"^eyJ[A-Za-z0-9_-]{8,}\."),
    # sha256 / 密钥指纹（64 位 hex）与 md5 级摘要（32 位 hex）
    re.compile(r"^[0-9a-fA-F]{64}$"),
    re.compile(r"^[0-9a-fA-F]{32}$"),
    # 常见 API key 前缀与 Authorization 头
    re.compile(r"(?i)^(?:sk-|zvk_|bearer\s+\S)"),
)

# Data-dictionary / system objects. The console is for the application schema;
# reading SYS/DBA_/V$ tables is how a "read-only" admin query becomes a
# cross-schema dump or a DB-host SSRF primitive.
_SYSTEM_OBJECT_RE = re.compile(
    r"(?i)(?<![\w$#])(?:SYS|DBA_[A-Z0-9_]+|V\$[A-Z0-9_]+|"
    r"GV\$[A-Z0-9_]+|ALL_[A-Z0-9_]+|USER\$[A-Z0-9_$#]*)(?![\w$#])"
)
_DANGEROUS_FUNCTION_RE = re.compile(
    r"(?i)(?<![\w$#])(?:UTL_[A-Z0-9_]+|DBMS_[A-Z0-9_]+|HTTPURITYPE)(?![\w$#])"
)
_PROTECTED_READ_TABLE_RE = re.compile(
    r"(?i)(?<![\w$#])(?:DUAL_AUTH_USES|USER_TRASH|TEMP_LOGIN_PASSWORDS|"
    r"DEVICE_CHALLENGES)(?![\w$#])"
)

# 破坏性语句里「目标表名」位于这些关键字之后；顺序即匹配优先级。
_TARGET_PATTERNS: dict[str, str] = {
    "DELETE": r"^DELETE\s+FROM\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "UPDATE": r"^UPDATE\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "INSERT": r"^INSERT\s+INTO\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "MERGE": r"^MERGE\s+INTO\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "DROP": r"^DROP\s+(?:TABLE|VIEW|INDEX|SEQUENCE|USER)\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "TRUNCATE": r"^TRUNCATE\s+TABLE\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "ALTER": r"^ALTER\s+(?:TABLE|VIEW|INDEX|SEQUENCE)\s+([A-Za-z][A-Za-z0-9_$#]*)",
    "CREATE": (
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?"
        r"(?:TABLE|VIEW|INDEX|SEQUENCE)\s+([A-Za-z][A-Za-z0-9_$#]*)"
    ),
}

# 摘要里夹在动词和目标名之间的名词，保留下来审计才读得懂。
_SUMMARY_NOUNS = frozenset(
    {"TABLE", "VIEW", "INDEX", "SEQUENCE", "FROM", "INTO", "OR", "REPLACE"}
)

_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# Oracle 用 ``''`` 转义单引号，所以字面量的正确形态是 (非引号 | 两个引号)*。
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

# 只读端点上 ``FOR UPDATE`` 会给行加锁并持有到会话结束，足以用一次「查询」
# 把其它事务堵住，因此需要单独识别（它不在写关键字黑名单里）。
_FOR_UPDATE_RE = re.compile(r"\bFOR\s+UPDATE\b", re.I)


class DbContentError(Exception):
    """内容管理动作被拒；路由层转 ``HTTPException(status_code, detail)``。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --------------------------------------------------------------------------- #
# 标识符
# --------------------------------------------------------------------------- #


def validate_identifier(raw: str, *, what: str = "标识符") -> str:
    """校验并**大写化**一个表/列名。

    大写是 Oracle 的默认语义（未加引号的标识符一律大写存储）；数据字典里也是
    大写，所以后续存在性校验、拼 SQL 都用这个形态。
    """
    if not isinstance(raw, str) or not _IDENTIFIER_RE.fullmatch(raw):
        raise DbContentError(
            400, f"{what}不合法：需 1-30 位、字母开头，仅含字母/数字/_$#"
        )
    return raw.upper()


def quote_identifier(raw: str) -> str:
    """已校验标识符 → 双引号形态。**调用前**必须过 :func:`validate_identifier`。"""
    return f'"{validate_identifier(raw)}"'


def _is_sensitive_column(name: str) -> bool:
    upper = str(name).upper()
    return (
        upper in _SENSITIVE_COLUMNS
        or upper.endswith("_HASH")
        or "PASSWORD" in upper
        or "SECRET" in upper
    )


def _looks_like_credential(value: str) -> bool:
    """值的形状就是凭据（散列 / JWT / 长 hex 指纹 / token 前缀）。"""
    return any(pattern.match(value) is not None for pattern in _CREDENTIAL_VALUE_PATTERNS)


def _redact_cell(column: str, value: Any) -> Any:
    """Hide credential material while keeping the row/column shape stable.

    列名命中之外再按值兜底：别名（``SELECT TOKEN_HASH AS x``）会把敏感列伪装成
    普通列名，只有值本身还能说明它是凭据。
    """
    if value is None:
        return None
    if _is_sensitive_column(column):
        return "***"
    if isinstance(value, str) and _looks_like_credential(value):
        return "***"
    return value


# --------------------------------------------------------------------------- #
# 语句分析（纯函数，可脱离会话测试）
# --------------------------------------------------------------------------- #


def strip_comments(sql: str) -> str:
    """去掉 ``--`` 行注释与 ``/* */`` 块注释。

    先剥注释再判关键字：否则 ``SELECT 1 -- DELETE`` 会被误杀，而
    ``/* x */ DELETE ...`` 会漏杀。
    """
    without_block = _BLOCK_COMMENT_RE.sub(" ", sql)
    return _LINE_COMMENT_RE.sub(" ", without_block)


def strip_string_literals(sql: str) -> str:
    """把字符串字面量替换成空串，只用于关键字扫描。

    目的：``SELECT ... WHERE name = 'DELETE'`` 是合法只读查询，不能因为字面量里
    出现关键字就拒。反过来也不会漏——字面量里的 ``;`` 或 ``DROP`` 本来就不是可
    执行语法。
    """
    return _STRING_LITERAL_RE.sub("''", sql)


def validate_readonly_sql(sql: str) -> str:
    """只读闸：返回去注释后的单条语句，或抛 :class:`DbContentError`。"""
    body = strip_comments(sql).strip()
    if not body:
        raise DbContentError(400, "SQL 不能为空")
    # 分号是最廉价的多语句拼接手段；先拒分号再判开头，顺序无关但错误提示更准。
    if ";" in body:
        raise DbContentError(400, "只允许单条语句：SQL 中不能包含分号")

    scannable = strip_string_literals(body)
    if not re.match(r"^(SELECT|WITH)\b", scannable, re.I):
        raise DbContentError(400, "只读查询必须以 SELECT 或 WITH 开头")

    hit = _FORBIDDEN_RE.search(scannable.upper())
    if hit is not None:
        # 这里挡的是 ``WITH x AS (...) DELETE ...`` 这类「以只读开头、后续写表」
        # 的形态：只判开头不够。
        raise DbContentError(400, f"只读查询不允许包含 {hit.group(1)}")

    if _SYSTEM_OBJECT_RE.search(scannable):
        raise DbContentError(403, "只读查询不允许访问 SYS/DBA_/V$/ALL_ 系统对象")
    if _DANGEROUS_FUNCTION_RE.search(scannable):
        raise DbContentError(403, "只读查询不允许调用 UTL_/DBMS_/HTTPURITYPE 等外部函数")
    if _PROTECTED_READ_TABLE_RE.search(scannable):
        raise DbContentError(403, "该表包含凭据/审批数据，禁止通过通用查询读取")
    if "@" in scannable:
        raise DbContentError(403, "只读查询不允许使用数据库链接")

    # ``FOR UPDATE`` 不是写语句，因此不在关键字黑名单里，但它会在只读端点上
    # 加行锁并持有到会话结束——一次「查询」就能把其它事务堵住，单独拒绝。
    if _FOR_UPDATE_RE.search(scannable):
        raise DbContentError(400, "只读查询不允许使用 FOR UPDATE 加锁")
    return body


def _target_table(scannable: str, verb: str) -> str | None:
    pattern = _TARGET_PATTERNS.get(verb)
    if pattern is None:
        return None
    match = re.match(pattern, scannable, re.I)
    return match.group(1).upper() if match else None


def _is_destructive(verb: str, scannable: str) -> bool:
    """Return True for every statement that can widen beyond one typed row.

    ``WHERE`` is not a safety proof: ``WHERE 1=1`` is a full-table write. Any
    UPDATE/DELETE/MERGE therefore needs the target-table confirmation, and so
    does an ALTER that drops/renames/modifies a column.
    """
    if verb in {"DROP", "TRUNCATE", "UPDATE", "DELETE", "MERGE"}:
        return True
    if verb == "ALTER" and re.search(
        r"\b(?:DROP|RENAME|MODIFY|SET\s+UNUSED)\b", scannable, re.I
    ):
        return True
    return False


def _summarize(statement: str, verb: str, target: str | None) -> str:
    """``DROP TABLE USERS`` 这种「动词 + 名词 + 目标」的短摘要（≤500 字）。

    刻意丢掉后面所有内容：摘要要进审计与响应，不该把整条 INSERT 的列值带出去。
    """
    words = statement.split()
    if target and len(words) >= 2:
        head = [verb]
        for word in words[1:4]:
            upper = word.upper()
            if upper in _SUMMARY_NOUNS:
                head.append(upper)
            else:
                break
        head.append(target.upper())
        return " ".join(head)[:500]
    return statement[:500]


def validate_ddl_sql(sql: str, confirm: str | None) -> dict[str, Any]:
    """``/ddl`` 的静态闸：返回执行计划，或抛 :class:`DbContentError`。

    返回的 ``sql`` 已去掉结尾分号（Oracle 不需要），内部若有分号直接拒——
    契约 §4.4.4 只允许一条语句，而 ``BEGIN ... END;`` 这类匿名块天然带内部分号，
    顺带被挡在外面。
    """
    body = strip_comments(sql).strip()
    if not body:
        raise DbContentError(400, "SQL 不能为空")

    body = body.rstrip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        raise DbContentError(400, "只允许单条语句：SQL 内部不能包含分号")

    statement = re.sub(r"\s+", " ", body)
    scannable = strip_string_literals(statement)

    match = re.match(r"^([A-Za-z]+)\b", scannable)
    if match is None:
        raise DbContentError(400, "无法识别的 SQL 语句")
    verb = match.group(1).upper()
    if verb not in _DDL_VERBS and verb not in _DML_VERBS and verb not in _READONLY_VERBS:
        raise DbContentError(400, f"该端点不支持 {verb} 语句")

    # The write console is not a SQL*Plus: reject statement classes that change
    # the session/instance or the security model. `CREATE USER`, `GRANT DBA`
    # and `ALTER SYSTEM` were all accepted by the old first-verb-only check.
    if verb in {"GRANT", "REVOKE", "COMMENT", "RENAME"}:
        raise DbContentError(403, f"该端点不允许 {verb} 语句")
    if verb in _READONLY_VERBS:
        raise DbContentError(400, "写端点只接受 DDL/DML；只读查询请使用 /query")
    if verb == "ALTER" and re.match(
        r"^ALTER\s+(?:SESSION|SYSTEM)\b", scannable, re.I
    ):
        raise DbContentError(403, "不允许通过内容控制台修改会话或实例")
    if verb == "CREATE" and not re.match(
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|INDEX|SEQUENCE)\b",
        scannable,
        re.I,
    ):
        raise DbContentError(403, "只允许创建表/视图/索引/序列")
    if verb == "DROP" and not re.match(
        r"^DROP\s+(?:TABLE|VIEW|INDEX|SEQUENCE)\b", scannable, re.I
    ):
        raise DbContentError(403, "只允许删除表/视图/索引/序列")
    if verb == "TRUNCATE" and not re.match(
        r"^TRUNCATE\s+TABLE\b", scannable, re.I
    ):
        raise DbContentError(403, "TRUNCATE 只允许用于表")

    target = _target_table(scannable, verb)
    upper = scannable.upper()

    if verb not in _READONLY_VERBS:
        for name in _PROTECTED_TABLES:
            # 词边界扫全句而不是只看目标表：``INSERT INTO dual_auth_uses ...``
            # 的目标表就是它，但 ``DELETE FROM x WHERE y IN (SELECT ... FROM
            # dual_auth_uses)`` 这种也要拦。
            if re.search(rf"\b{name}\b", upper):
                raise DbContentError(
                    403, f"{name.lower()} 属于系统保护表，禁止通过该接口修改"
                )
        if target == "USERS" and verb in {"DROP", "TRUNCATE"}:
            raise DbContentError(403, "users 表禁止 DROP/TRUNCATE")

    destructive = _is_destructive(verb, scannable)
    if destructive:
        if not target:
            raise DbContentError(400, "无法识别破坏性语句的目标表名")
        # 契约 §4.4.3：confirm 必须**等于目标表名**。只做大小写无关比较，
        # 不做前缀/包含匹配——「差不多对」在这里等于没确认。
        if not confirm or confirm.strip().upper() != target:
            raise DbContentError(400, f'破坏性语句需要 confirm="{target}"')

    return {
        "sql": body,
        "verb": verb,
        "target": target,
        "summary": _summarize(statement, verb, target),
        "destructive": destructive,
        "is_ddl": verb in _DDL_VERBS,
    }


# --------------------------------------------------------------------------- #
# 取值序列化
# --------------------------------------------------------------------------- #


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lob_kind_of_object(lob: Any) -> str:
    """``oracledb.LOB`` → ``CLOB`` / ``BLOB``；认不出类型时按 CLOB 处理。"""
    name = str(getattr(getattr(lob, "type", None), "name", "")).upper()
    return "BLOB" if "BLOB" in name else "CLOB"


def _lob_payload(value: Any, kind: str) -> dict[str, Any]:
    """LOB 值的统一形态：``{"$lob","length","preview"}``（契约 §4.5）。

    BLOB 的 preview 恒为 ``None``：把二进制塞进 JSON 既浪费带宽，前端也没法渲染。
    """
    kind = "BLOB" if kind.upper() == "BLOB" else "CLOB"
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {"$lob": kind, "length": len(raw), "preview": None}
    if isinstance(value, str):
        return {"$lob": kind, "length": len(value), "preview": value[:LOB_PREVIEW_CHARS]}

    reader = getattr(value, "read", None)
    if not callable(reader):
        return {"$lob": kind, "length": None, "preview": None}

    length = _int_or_none(getattr(value, "size", None))
    if kind == "BLOB":
        return {"$lob": kind, "length": length, "preview": None}

    try:
        # oracledb 的 LOB.read 只接受 1-based offset；读整段再截断会白拉 10MB，
        # 所以直接向驱动要前 2KB。
        preview = reader(offset=1, amount=LOB_PREVIEW_CHARS)
        preview = "" if preview is None else str(preview)
    except Exception:  # noqa: BLE001 - LOB 已释放/连接已断：宁可少信息也不 500
        preview = ""
    if not length:
        # 驱动没给 size（0/None）时用实际读到的长度兜底，别回一个假的 0。
        length = len(preview)
    return {"$lob": kind, "length": length, "preview": preview[:LOB_PREVIEW_CHARS]}


def _default_text(value: Any) -> str | None:
    """``user_tab_columns.data_default``（LONG）→ 去空白的字符串。"""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        text_value = bytes(value).decode("utf-8", "replace")
    elif isinstance(value, str):
        text_value = value
    else:
        reader = getattr(value, "read", None)
        if not callable(reader):
            text_value = str(value)
        else:
            try:
                text_value = str(reader())
            except Exception:  # noqa: BLE001
                return None
    text_value = text_value.strip()
    return text_value or None


def serialize_value(value: Any) -> Any:
    """单个单元格 → JSON 安全值（契约 §4.5：NUMBER→number，TIMESTAMP→ISO8601）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        # Decimal 交给 FastAPI 的默认编码器会变成字符串（小数）或 float（整数），
        # 两种形态不一致；这里显式统一成 JSON number。
        return (
            int(value)
            if value == value.to_integral_value()
            else float(value)
        )
    if isinstance(value, (datetime, date, time_of_day)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _lob_payload(value, "BLOB")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    reader = getattr(value, "read", None)
    if callable(reader):
        return _lob_payload(value, _lob_kind_of_object(value))
    return value


_LOB_TYPES = frozenset({"CLOB", "NCLOB", "BLOB"})


def _serialize_cell(value: Any, data_type: str) -> Any:
    """有列元数据时按列类型判定 LOB，而不是靠值的 Python 类型猜。

    原因：同一个 CLOB 列在不同驱动/精度下可能回 ``str`` 也可能回 ``LOB`` 对象，
    而 ``VARCHAR2`` 永远是 ``str``——只有列类型能可靠区分。
    """
    base = (data_type or "").split("(")[0].strip().upper()
    if base in _LOB_TYPES and value is not None:
        return _lob_payload(value, base)
    return serialize_value(value)


def _rows_as_dicts(columns: list[str], rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {name: serialize_value(value) for name, value in zip(columns, row)}
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# 数据字典
# --------------------------------------------------------------------------- #

_TABLE_SQL = "SELECT table_name, num_rows FROM user_tables WHERE table_name = :name"
_VIEW_SQL = "SELECT view_name FROM user_views WHERE view_name = :name"
_SCHEMA_TABLES_SQL = (
    "SELECT table_name, num_rows, last_analyzed FROM user_tables ORDER BY table_name"
)
_SCHEMA_VIEWS_SQL = "SELECT view_name FROM user_views ORDER BY view_name"
_COLUMNS_SQL = (
    "SELECT column_name, data_type, data_length, nullable, data_default "
    "FROM user_tab_columns WHERE table_name = :name ORDER BY column_id"
)
_PRIMARY_KEY_SQL = (
    "SELECT cc.column_name FROM user_constraints c "
    "JOIN user_cons_columns cc ON cc.constraint_name = c.constraint_name "
    "WHERE c.table_name = :name AND c.constraint_type = 'P' "
    "ORDER BY cc.position"
)


async def _table_info(session: Any, name: str) -> dict[str, Any] | None:
    """查一次表、查一次视图（表命中就不再查视图）。"""
    result = await session.execute(text(_TABLE_SQL), {"name": name})
    row = result.first()
    if row is not None:
        return {
            "name": str(row[0]),
            "type": "TABLE",
            "num_rows": _int_or_none(row[1]),
        }

    result = await session.execute(text(_VIEW_SQL), {"name": name})
    row = result.first()
    if row is not None:
        # 视图没有统计信息，``num_rows`` 只能是 null（不做 COUNT(*)）。
        return {"name": str(row[0]), "type": "VIEW", "num_rows": None}
    return None


async def require_table(session: Any, raw_name: str) -> dict[str, Any]:
    """校验表名格式 + 存在性；不存在 → 404（契约 §4.2）。"""
    name = validate_identifier(raw_name, what="表名")
    info = await _table_info(session, name)
    if info is None:
        raise DbContentError(404, f"表或视图不存在：{name}")
    return info


async def _column_rows(session: Any, table: str) -> list[tuple[Any, ...]]:
    result = await session.execute(text(_COLUMNS_SQL), {"name": table})
    return list(result.fetchall())


async def _primary_key(session: Any, table: str) -> list[str]:
    result = await session.execute(text(_PRIMARY_KEY_SQL), {"name": table})
    return [str(row[0]).upper() for row in result.fetchall()]


async def list_schema(session: Any) -> dict[str, Any]:
    """表/视图清单（契约 §4.5：``num_rows``/``last_analyzed`` 允许 null）。"""
    tables: list[dict[str, Any]] = []

    result = await session.execute(text(_SCHEMA_TABLES_SQL))
    for name, num_rows, last_analyzed in result.fetchall():
        tables.append(
            {
                "name": str(name),
                "type": "TABLE",
                "num_rows": _int_or_none(num_rows),
                "last_analyzed": serialize_value(last_analyzed),
            }
        )

    result = await session.execute(text(_SCHEMA_VIEWS_SQL))
    for (name,) in result.fetchall():
        tables.append(
            {
                "name": str(name),
                "type": "VIEW",
                "num_rows": None,
                "last_analyzed": None,
            }
        )

    tables.sort(key=lambda item: item["name"])
    return {"total": len(tables), "tables": tables}


async def get_columns(session: Any, raw_name: str) -> dict[str, Any]:
    """列定义 + 主键清单（契约 §4.5）。``primary_key`` 为空 = 无主键表。"""
    info = await require_table(session, raw_name)
    table = info["name"]

    primary_key = await _primary_key(session, table)
    in_pk = set(primary_key)

    columns = []
    for name, data_type, data_length, nullable, default in await _column_rows(
        session, table
    ):
        column_name = str(name).upper()
        columns.append(
            {
                "name": column_name,
                "type": str(data_type),
                "length": _int_or_none(data_length),
                "nullable": str(nullable).upper() != "N",
                "default": _default_text(default),
                "primary_key": column_name in in_pk,
            }
        )

    return {"table": table, "columns": columns, "primary_key": primary_key}


# --------------------------------------------------------------------------- #
# 行读取
# --------------------------------------------------------------------------- #


async def read_rows(
    session: Any,
    raw_name: str,
    *,
    offset: int = 0,
    limit: int = 100,
    order_by: str | None = None,
    desc: bool = False,
) -> dict[str, Any]:
    """按页读取整表（契约 §4.5）。"""
    limit = max(1, min(int(limit), MAX_ROW_LIMIT))
    offset = max(0, int(offset))

    info = await require_table(session, raw_name)
    table = info["name"]
    if table in _PROTECTED_READ_TABLES:
        raise DbContentError(403, "该表包含凭据/审批数据，禁止通过通用内容读取")

    column_rows = await _column_rows(session, table)
    columns = [str(row[0]).upper() for row in column_rows]
    if not columns:
        raise DbContentError(404, f"表或视图没有可读列：{table}")

    primary_key = await _primary_key(session, table)

    if order_by is None:
        # 没有 ORDER BY 时 Oracle 的行序不稳定，翻页会重复/漏行；有主键就按主键
        # 定序，这也是唯一「业务上唯一」的排序列。
        order_columns = list(primary_key)
        direction = "ASC"
    else:
        wanted = validate_identifier(order_by, what="排序列")
        if wanted not in columns:
            raise DbContentError(400, f"排序列不存在：{wanted}")
        order_columns = [wanted]
        direction = "DESC" if desc else "ASC"

    sql = f"SELECT {', '.join(quote_identifier(name) for name in columns)} "
    sql += f"FROM {quote_identifier(table)}"
    if order_columns:
        sql += " ORDER BY " + ", ".join(
            f"{quote_identifier(name)} {direction}" for name in order_columns
        )
    # 多取一行判 has_more（契约 §4.5）；COUNT(*) 会全表扫，代价与收益完全不成比例。
    sql += " OFFSET :row_offset ROWS FETCH NEXT :row_fetch ROWS ONLY"

    result = await session.execute(
        text(sql), {"row_offset": offset, "row_fetch": limit + 1}
    )
    fetched = list(result.fetchall())
    has_more = len(fetched) > limit
    page = fetched[:limit]

    return {
        "table": table,
        "columns": columns,
        "rows": [
            {
                name: _redact_cell(
                    name, _serialize_cell(value, str(column_rows[index][1]))
                )
                for index, (name, value) in enumerate(zip(columns, row))
            }
            for row in page
        ],
        "primary_key": primary_key,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "total_estimate": info["num_rows"],
    }


# --------------------------------------------------------------------------- #
# 行写入（全部按主键定位）
# --------------------------------------------------------------------------- #


async def _table_columns(session: Any, table: str) -> dict[str, str]:
    """列名 → 列类型（均已大写）；写路径据此把前端值转成驱动可绑定的类型。"""
    return {
        str(row[0]).upper(): str(row[1]).upper()
        for row in await _column_rows(session, table)
    }


def _temporal_kind(data_type: str) -> str | None:
    """Classify a ``USER_TAB_COLUMNS.DATA_TYPE`` value.

    * ``naive`` — ``DATE`` / plain ``TIMESTAMP[(p)]``; project convention is a
      naive local timestamp (``docs/ADB_SCHEMA_SPEC.md`` §0).
    * ``ltz`` — ``TIMESTAMP[(p)] WITH LOCAL TIME ZONE``; Oracle stores UTC and
      returns session-local time.
    * ``tz`` — ``TIMESTAMP[(p)] WITH TIME ZONE``; the zone offset is part of the
      column value.

    ``None`` means "not a temporal type" (no coercion).
    """
    compact = re.sub(r"\([^)]*\)", " ", data_type or "")
    compact = re.sub(r"\s+", " ", compact).strip().upper()
    if compact == "DATE":
        return "naive"
    if not compact.startswith("TIMESTAMP"):
        return None
    if "WITH LOCAL TIME ZONE" in compact:
        return "ltz"
    if "WITH TIME ZONE" in compact:
        return "tz"
    return "naive"


def _coerce_value(column: str, value: Any, data_type: str) -> Any:
    """把前端值转成 Oracle 列要求的类型。

    网格读出来的朴素 ``DATE``/``TIMESTAMP`` 是 ISO8601 字符串
    （``…T12:00:00``），编辑后原样送回来。若直接按字符串绑定，Oracle 会按
    ``NLS_DATE_FORMAT`` / ``NLS_TIMESTAMP_FORMAT`` 做隐式转换并抛
    ``ORA-01861``——即整页写操作 500。这里在进入驱动前转成 ``datetime``；
    非法值走 400，而不是把数据库错误漏成 500。

    时区列按列类型分开处理（python-oracledb 对所有 timestamp 都返回 naive 值，
    无法可靠往返 TSTZ）：

    * ``TIMESTAMP WITH TIME ZONE``：内容管理页无法保留偏移，**拒绝写入**，
      避免悄悄改写时间点；
    * ``TIMESTAMP WITH LOCAL TIME ZONE``：接受 UI 读回来的 naive 会话本地时间
      （Oracle 会按数据库时区归一化），拒绝带偏移的输入，避免二次换算；
    * 朴素列：保持原策略，带偏移的输入按 UTC 归一化为 naive 值。
    """
    kind = _temporal_kind(data_type)
    if value is None or kind is None:
        return value

    if kind == "tz":
        raise DbContentError(
            400,
            f"列 {column} 是 TIMESTAMP WITH TIME ZONE，内容管理页不支持写入（会丢失时区信息）",
        )

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time_of_day.min)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise DbContentError(
                400, f"列 {column} 的时间格式不合法，需为 ISO8601"
            ) from None
    else:
        raise DbContentError(400, f"列 {column} 的时间格式不合法，需为 ISO8601")

    if kind == "ltz" and parsed.tzinfo is not None:
        raise DbContentError(
            400,
            f"列 {column} 是 TIMESTAMP WITH LOCAL TIME ZONE，只接受无时区时间",
        )

    # 朴素列没有时区语义；带偏移的输入按 UTC 归一化后落成 naive 值。
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _split_columns(
    payload: dict[str, Any], known: dict[str, str], *, what: str
) -> list[tuple[str, Any]]:
    """``{列名: 值}`` → ``[(大写列名, 值)]``，未知名/重复名一律 400。

    列名只用于拼接 SQL 标识符；值按列类型归一化后走绑定参数。这里不碰 SQL 文本。
    """
    if not payload:
        raise DbContentError(400, f"{what}不能为空")

    seen: set[str] = set()
    items: list[tuple[str, Any]] = []
    for raw_name, value in payload.items():
        name = validate_identifier(str(raw_name), what=f"{what}列名")
        if name in seen:
            raise DbContentError(400, f"{what}列名重复：{name}")
        if name not in known:
            raise DbContentError(400, f"列不存在：{name}")
        seen.add(name)
        items.append((name, _coerce_value(name, value, known[name])))
    return items


def _check_key(key: dict[str, Any], primary_key: list[str]) -> list[tuple[str, Any]]:
    """``key`` 必须**恰好**覆盖全部主键列；返回按主键顺序排列的绑定项。"""
    if not primary_key:
        # 无主键表不能按行定位：WHERE 只能退化成分页条件，一不小心就是全表写。
        raise DbContentError(400, "该表没有主键，不支持按主键更新/删除")
    if not key:
        raise DbContentError(400, "key 不能为空")

    provided: dict[str, Any] = {}
    for raw_name, value in key.items():
        name = validate_identifier(str(raw_name), what="key 列名")
        if name in provided:
            raise DbContentError(400, f"key 列名重复：{name}")
        provided[name] = value

    # 先报缺列：它是最常见的错误，也最能说明「按主键定位」的要求。
    missing = [name for name in primary_key if name not in provided]
    if missing:
        raise DbContentError(400, "key 缺少主键列：" + ", ".join(missing))
    extra = [name for name in provided if name not in primary_key]
    if extra:
        # 允许非唯一列出现在 WHERE 里等于放开了「按非唯一条件批量改」的口子。
        raise DbContentError(400, "key 只能包含主键列：" + ", ".join(extra))
    return [(name, provided[name]) for name in primary_key]


async def _writable_table(session: Any, raw_name: str) -> str:
    """行 CRUD 的表闸：自保护表一律 403（契约 §4.4.2 的「任何 DML」部分）。

    行 CRUD 也是 DML：``DELETE /table/DUAL_AUTH_USES/rows`` 能删掉重放记录，
    效果与 ``DELETE FROM dual_auth_uses`` 完全一样，所以必须走同一道闸。
    """
    table = (await require_table(session, raw_name))["name"]
    if table in _PROTECTED_TABLES:
        raise DbContentError(
            403, f"{table.lower()} 属于系统保护表，禁止通过该接口修改"
        )
    return table


async def _column_names_checked(
    session: Any, raw_name: str
) -> tuple[str, dict[str, str]]:
    table = await _writable_table(session, raw_name)
    return table, await _table_columns(session, table)


def _assignments(prefix: str, items: list[tuple[str, Any]]) -> tuple[str, dict[str, Any]]:
    """``[(列, 值)]`` → SQL 片段 + 绑定参数（参数名与列名解耦，避免名字冲突）。"""
    clauses = []
    params: dict[str, Any] = {}
    for index, (name, value) in enumerate(items):
        placeholder = f"{prefix}{index}"
        clauses.append(f"{quote_identifier(name)} = :{placeholder}")
        params[placeholder] = value
    return ", ".join(clauses), params


async def insert_row(
    session: Any, raw_name: str, values: dict[str, Any]
) -> dict[str, Any]:
    table, known = await _column_names_checked(session, raw_name)
    items = _split_columns(values, known, what="插入列")

    columns = ", ".join(quote_identifier(name) for name, _ in items)
    # INSERT 要的是纯占位符列表（``"COL" = :v0`` 是 UPDATE 的语法），所以这里
    # 不走 ``_assignments``，但参数名规则保持一致：位置索引 + 前缀。
    placeholders = ", ".join(f":v{index}" for index in range(len(items)))
    params = {f"v{index}": value for index, (_, value) in enumerate(items)}

    result = await session.execute(
        text(f"INSERT INTO {quote_identifier(table)} ({columns}) VALUES ({placeholders})"),
        params,
    )
    await session.commit()
    return {"rowcount": _rowcount_of(result)}


async def update_row(
    session: Any, raw_name: str, key: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    table, known = await _column_names_checked(session, raw_name)
    primary_key = await _primary_key(session, table)
    key_items = _check_key(key, primary_key)
    value_items = _split_columns(values, known, what="更新列")

    set_clause, set_params = _assignments("s", value_items)
    where_clause, key_params = _assignments("k", key_items)
    # SET 与 WHERE 用不同前缀：同名列（例如把主键改掉）不会互相覆盖绑定值。
    result = await session.execute(
        text(
            f"UPDATE {quote_identifier(table)} SET {set_clause} WHERE {where_clause}"
        ),
        {**set_params, **key_params},
    )
    await session.commit()
    return {"rowcount": _rowcount_of(result)}


async def delete_row(
    session: Any, raw_name: str, key: dict[str, Any]
) -> dict[str, Any]:
    # 删除只需要表名与主键；不必再拉整份列清单。
    table = await _writable_table(session, raw_name)
    primary_key = await _primary_key(session, table)
    key_items = _check_key(key, primary_key)

    where_clause, key_params = _assignments("k", key_items)
    result = await session.execute(
        text(f"DELETE FROM {quote_identifier(table)} WHERE {where_clause}"), key_params
    )
    await session.commit()
    return {"rowcount": _rowcount_of(result)}


def _rowcount_of(result: Any) -> int:
    rowcount = getattr(result, "rowcount", 0)
    try:
        rowcount = int(rowcount)
    except (TypeError, ValueError):
        return 0
    return rowcount if rowcount > 0 else 0


# --------------------------------------------------------------------------- #
# 只读查询
# --------------------------------------------------------------------------- #


async def _set_call_timeout(session: Any, seconds: float) -> None:
    """尽力把驱动的 ``call_timeout`` 设成 30s（服务端可中断）。

    替身会话/非 oracledb 驱动会在这里静默失败：外层 ``wait_for`` 仍然保证请求
    不会无限挂住，只是无法让数据库侧真正取消语句。
    """
    try:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        driver = getattr(raw, "driver_connection", None)
        if driver is not None:
            driver.call_timeout = int(seconds * 1000)
    except Exception:  # noqa: BLE001 - 超时兜底不是关键路径
        return


async def _execute_user_sql(session: Any, sql: str) -> Any:
    """把用户 SQL 原样交给驱动执行。

    ``/query`` 与 ``/ddl`` 都不接受绑定参数，所以 SQL 里的 ``:`` 必须是字面量。
    若走 SQLAlchemy 的 ``text()``，它会把形如 ``:name`` 的片段当成绑定参数（连
    字符串字面量里也不放过），这类合法 Oracle SQL（JSON/时间格式/注释等）会在
    真正到达数据库前抛 ``StatementError`` → 500。驱动级执行既保留了只读闸与
    标识符闸（都在这之前），也让控制台的行为符合「输入什么就执行什么」。
    """
    connection = await session.connection()
    return await connection.exec_driver_sql(sql)


async def run_query(session: Any, sql: str, *, limit: int = 100) -> dict[str, Any]:
    """只读查询（契约 §4.3/§4.5）：无双人码，但有严格只读闸。"""
    body = validate_readonly_sql(sql)
    limit = max(1, min(int(limit), MAX_QUERY_LIMIT))

    await _set_call_timeout(session, QUERY_TIMEOUT_SECONDS)

    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            _execute_user_sql(session, body), timeout=QUERY_TIMEOUT_SECONDS
        )
        # 只 fetchmany：``fetchall`` 会把一条 ``SELECT * FROM 大表`` 整体拉进内存。
        rows = list(result.fetchmany(limit + 1))
        columns = [str(key) for key in result.keys()]
    except asyncio.TimeoutError:
        raise DbContentError(504, f"查询超时（{QUERY_TIMEOUT_SECONDS} 秒）") from None
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    truncated = len(rows) > limit
    rows = rows[:limit]
    dict_rows = _rows_as_dicts(columns, rows)
    for row in dict_rows:
        for column in columns:
            if column in row:
                row[column] = _redact_cell(column, row[column])
    return {
        "columns": columns,
        "rows": dict_rows,
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }


# --------------------------------------------------------------------------- #
# DDL / 写语句
# --------------------------------------------------------------------------- #


async def run_ddl(session: Any, sql: str, confirm: str | None = None) -> dict[str, Any]:
    """执行单条 DDL/DML 并提交（契约 §4.4/§4.5）。

    ``committed`` 恒为 ``true``：Oracle 的 DDL 隐式提交，这里再显式 commit 一次
    只是把 DML 也收进同一语义；前端必须明确告诉用户「已提交、不可回滚」。
    """
    plan = validate_ddl_sql(sql, confirm)

    result = await _execute_user_sql(session, plan["sql"])
    await session.commit()

    rowcount = None if plan["is_ddl"] else _rowcount_of(result)
    return {"rowcount": rowcount, "committed": True, "summary": plan["summary"]}


__all__ = [
    "DbContentError",
    "LOB_PREVIEW_CHARS",
    "MAX_QUERY_LIMIT",
    "MAX_ROW_LIMIT",
    "QUERY_TIMEOUT_SECONDS",
    "delete_row",
    "get_columns",
    "insert_row",
    "list_schema",
    "quote_identifier",
    "read_rows",
    "require_table",
    "run_ddl",
    "run_query",
    "serialize_value",
    "session_scope",
    "strip_comments",
    "strip_string_literals",
    "update_row",
    "validate_ddl_sql",
    "validate_identifier",
    "validate_readonly_sql",
]
