"""Oracle implementation of :class:`db.repos.protocols.UsersRepo`.

SQLAlchemy 2.0 async Core over the ``USERS`` table (``zvms_new.users``). Column
names follow the rename map in ``docs/ADB_SCHEMA_SPEC.md`` §3 (``_id``→``OID``,
``id``→``BUSINESS_ID``, ``class_id``→``CLASS_OID``, ``password``→``PASSWORD_HASH``);
returned rows are translated back to the Mongo document shape.

Session ownership
-----------------
``__init__`` takes an optional ``AsyncSession``:

* ``session is None`` (default): each method opens its own session through
  :func:`db.oracle.session.session_scope`, and **writes commit once** (one write
  = one transaction); the session is closed by ``session_scope``.
* ``session`` supplied: the repo uses it as-is and **never commits or closes**
  it — the caller owns the unit of work.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncIterator

from sqlalchemy import (
    Column,
    MetaData,
    Table,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.oracle import CLOB, TIMESTAMP, VARCHAR2
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import is_valid_oid, new_oid, normalize_oid
from db.models import activity_members as activity_members_tbl
from db.models import dual_auth_uses as dual_auth_uses_tbl
from db.models import files as files_tbl
from db.models import groups as groups_tbl
from db.models import time_academic_year as time_academic_year_tbl
from db.models import time_with_origin as time_with_origin_tbl
from db.models import user_device_bindings as user_device_bindings_tbl
from db.models import user_time as user_time_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# Role membership: Mongo ``{"roles": role}`` matches when the JSON array
# contains ``role``. ``$[*]?(@ == $r)`` tests every element against the value
# passed through PASSING, so the role stays a bound parameter.
_ROLE_EXISTS = (
    "JSON_EXISTS(users.roles, '$[*]?(@ == $r)' PASSING :role_value AS \"r\")"
)

# The Mongo documents use a class-less sentinel: the aggregation sets
# ``class_name`` to this literal when the group lookup misses.
_UNKNOWN_CLASS = "未知班级"

# Oracle refuses more than 1000 elements in a single ``IN`` (ORA-01795).
_OID_CHUNK = 1000


@asynccontextmanager
async def _session_for(
    session: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    """Yield ``(session, owns)``; ``owns`` is True only for repo-created ones."""
    if session is not None:
        yield session, False
        return
    async with session_scope() as owned:
        yield owned, True


def _user_filter(
    query: str, role: str, group_oid: str | None = None
) -> list[Any]:
    """Replicate the Mongo match stages shared by list/members/count queries.

    * ``role`` → array membership (JSON_EXISTS).
    * non-empty ``query`` → case-insensitive ``contains`` on ``name``/``id``,
      with the keyword ``re.escape``d exactly like the current pipelines.
    * ``group_oid`` → ``class_id`` equality.
    """
    conds: list[Any] = []
    if role:
        conds.append(text(_ROLE_EXISTS).bindparams(role_value=role))
    if query:
        pattern = re.escape(query)
        conds.append(
            or_(
                func.regexp_like(users_tbl.c.name, pattern, "i"),
                func.regexp_like(users_tbl.c.business_id, pattern, "i"),
            )
        )
    if group_oid is not None:
        conds.append(users_tbl.c.class_oid == group_oid)
    return conds


def _user_page_stmt(query: str, role: str, page: int, size: int):
    """``list_page`` select: users LEFT JOIN groups, ordered + paginated.

    Carries the password presence flag and the ``COUNT(*) OVER()`` total, so a
    page needs a single statement (the ``USERS``↔``GROUPS`` join is 1:1).
    """
    stmt = (
        select(
            users_tbl.c.oid,
            users_tbl.c.business_id,
            users_tbl.c.name,
            users_tbl.c.roles,
            users_tbl.c.class_oid,
            users_tbl.c.banned,
            groups_tbl.c.name.label("class_name"),
            users_tbl.c.password_hash,
            func.count().over().label("total_count"),
        )
        .select_from(
            users_tbl.outerjoin(
                groups_tbl, users_tbl.c.class_oid == groups_tbl.c.oid
            )
        )
    )
    conds = _user_filter(query, role)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(users_tbl.c.business_id.asc(), users_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _user_count_stmt(query: str, role: str):
    """``list_page`` count: same filter, no join/pagination needed."""
    stmt = select(func.count()).select_from(users_tbl)
    conds = _user_filter(query, role)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _banned_filter(query: str) -> list[Any]:
    """「当前被封禁」的谓词：闸门列 + 可选的姓名/学号包含匹配。

    复用 :func:`_user_filter` 的 ``query`` 语义（``re.escape`` + REGEXP_LIKE
    ``'i'``），只额外钉住 ``banned = 1``——两条列表的搜索行为必须一致，管理员
    才会觉得是同一个框。
    """
    return [users_tbl.c.banned == 1, *_user_filter(query, "")]


def _banned_page_stmt(query: str, page: int, size: int):
    """``list_banned_page`` 一页「当前被封」用户 + ``COUNT(*) OVER()`` 总数。

    与 ``_user_page_stmt`` 同一形状（LEFT JOIN ``GROUPS`` 取班级名、窗口计数、
    ``business_id``/``oid`` 升序），区别只是谓词换成 ``banned = 1`` 且投影
    带出封禁元数据：前端这一屏回答的是「现在谁进不来」，不是流水账。
    ``USERS``↔``GROUPS`` 是 1:1，窗口计数不会被 join 放大。
    """
    stmt = (
        select(
            users_tbl.c.oid,
            users_tbl.c.business_id,
            users_tbl.c.name,
            users_tbl.c.ban_reason,
            users_tbl.c.ban_until,
            users_tbl.c.ban_source,
            users_tbl.c.ban_rule,
            groups_tbl.c.name.label("class_name"),
            func.count().over().label("total_count"),
        )
        .select_from(
            users_tbl.outerjoin(
                groups_tbl, users_tbl.c.class_oid == groups_tbl.c.oid
            )
        )
        .where(*_banned_filter(query))
    )
    return (
        stmt.order_by(users_tbl.c.business_id.asc(), users_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _banned_count_stmt(query: str):
    """越界页补总数用；不需要 join，``GROUPS`` 不参与过滤。"""
    return (
        select(func.count())
        .select_from(users_tbl)
        .where(*_banned_filter(query))
    )


def _user_columns() -> tuple[Any, ...]:
    """The full USERS projection shared by the by-id / by-学号 lookups."""
    return (
        users_tbl.c.oid,
        users_tbl.c.business_id,
        users_tbl.c.name,
        users_tbl.c.roles,
        users_tbl.c.class_oid,
        users_tbl.c.password_hash,
        users_tbl.c.session_id,
        users_tbl.c.sex,
        users_tbl.c.eligibility,
        users_tbl.c.past,
        users_tbl.c.banned,
        users_tbl.c.ban_reason,
        users_tbl.c.ban_until,
        users_tbl.c.ban_source,
        users_tbl.c.ban_rule,
        users_tbl.c.primary_xuehai_id,
    )


def _get_by_oid_stmt(oid: str):
    return select(*_user_columns()).where(users_tbl.c.oid == oid)


def _find_by_business_id_stmt(business_id: str):
    return select(*_user_columns()).where(
        users_tbl.c.business_id == business_id
    )


def _insert_user_stmt(
    oid: str,
    business_id: str,
    name: str,
    roles: list[str],
    password_hash: str | None,
    class_oid: str | None,
):
    return insert(users_tbl).values(
        oid=oid,
        business_id=business_id,
        name=name,
        roles=roles,
        password_hash=password_hash,
        class_oid=class_oid,
    )


def _set_password_stmt(oid: str, password_hash: str | None):
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(password_hash=password_hash, session_id=new_oid())
    )


def _set_password_guarded_stmt(
    oid: str, password_hash: str, actor_oid: str
):
    """``set_password`` as one atomic guarded UPDATE.

    The write only lands when the actor is the user themself
    (``:actor_oid = users.oid``) or the target carries no ``admin`` role
    (``NOT JSON_EXISTS(...)``). Both the role test and the 学号/oid values stay
    bound parameters — nothing is string-concatenated.
    """
    guard = or_(
        text(":actor_oid = users.oid").bindparams(actor_oid=actor_oid),
        text("NOT (" + _ROLE_EXISTS + ")").bindparams(role_value="admin"),
    )
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid, guard)
        .values(password_hash=password_hash, session_id=new_oid())
    )


def _set_roles_stmt(oid: str, roles: list[str]):
    return update(users_tbl).where(users_tbl.c.oid == oid).values(roles=roles, session_id=new_oid())


def _set_business_id_stmt(oid: str, business_id: str):
    """学号 = 登录名：改完轮换 ``session_id``，旧 token 立即失效。

    与 ``_set_roles_stmt`` / ``_set_password_stmt`` 同一约定：任何影响「
    这个账号是谁 / 怎么进来」的写都轮换会话 id。
    """
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(business_id=business_id, session_id=new_oid())
    )


def _set_banned_stmt(oid: str, banned: int):
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(banned=banned, session_id=new_oid())
    )


def _set_ban_stmt(
    oid: str,
    reason: str,
    until: datetime | None,
    source: str,
    rule_code: str | None,
):
    """封禁一条 UPDATE：``banned`` 闸门 + 四列元数据 + 会话轮换。

    元数据必须和 ``banned=1`` 同一条语句落下：分两步写会让「已封禁但无原因」
    的中间态被并发的审核页面读到。``until=None`` 就是永久（列留 NULL），
    这正是「NULL 且 banned=1 表示永久」的写入半边。
    """
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(
            banned=1,
            ban_reason=reason,
            ban_until=until,
            ban_source=source,
            ban_rule=rule_code,
            session_id=new_oid(),
        )
    )


def _clear_ban_stmt(oid: str):
    """解封：闸门归零 + 四列清空。

    元数据不保留在 users 行上：解封后的账号不该看起来还带着原因/到期时间，
    历史事实由 ``BAN_RECORDS`` 流水承担。
    """
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(
            banned=0,
            ban_reason=None,
            ban_until=None,
            ban_source=None,
            ban_rule=None,
            session_id=new_oid(),
        )
    )


def _set_primary_xuehai_stmt(oid: str, xuehai_id: str):
    """绑定主学海设备——**不**轮换 ``session_id``。

    与 ``_set_business_id_stmt`` / ``_set_ban_stmt`` 刻意不同：主设备不是身份
    凭证，改它不该把账号在所有设备上踢下线（那会让「换台平板」变成一次全端
    重新登录）。真正的登录闸门在登录路径上按 ``primary_xuehai_id`` 判定，旧
    token 依然有效，只是下一次登录会要求从新设备发起。
    """
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(primary_xuehai_id=xuehai_id)
    )


def _clear_primary_xuehai_stmt(oid: str):
    """解绑主学海设备（列归 NULL = 未绑定，回到「不做设备校验」状态）。

    同样不轮换会话：解绑后账号反而更宽松，没必要让在线用户重新登录。
    """
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(primary_xuehai_id=None)
    )


def _list_banned_by_xuehai_stmt(xuehai_id: str):
    """主设备 = 该学海 ID 且 ``banned = 1`` 的用户（**只做原始筛选**）。

    ``ban_until`` 是否已过期不在这里判：到期封禁仍留着 ``banned=1`` 是正常
    存量状态，只有 ``util.ban.ban_is_active`` 能回答「此刻是否生效」。把时间
    比较搬进 SQL 会造出第二套判定口径，两边迟早漂移。
    """
    return select(*_user_columns()).where(
        users_tbl.c.primary_xuehai_id == xuehai_id,
        users_tbl.c.banned == 1,
    )


def _primary_owned_by_other_stmt(xuehai_id: str, user_oid: str):
    """该设备号是否**挂在别人名下**的主设备（``EXISTS`` 语义，取一行即止）。

    设备号来自请求头，可以被伪造成受害者的主设备号；封禁路径据此拒写
    ``BANNED_DEVICES``，免得 3 次错密码就能把任意人永久锁在门外。
    """
    return (
        select(users_tbl.c.oid)
        .where(
            users_tbl.c.primary_xuehai_id == xuehai_id,
            users_tbl.c.oid != user_oid,
        )
        .limit(1)
    )


def _set_class_stmt(user_oid: str, class_oid: str | None):
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == user_oid)
        .values(class_oid=class_oid, session_id=new_oid())
    )


def _get_past_stmt(oid: str):
    return select(users_tbl.c.past).where(users_tbl.c.oid == oid)


def _set_past_stmt(oid: str, past: list[str]):
    return update(users_tbl).where(users_tbl.c.oid == oid).values(past=past)


def _detach_class_stmt(class_oid: str):
    return (
        update(users_tbl)
        .where(users_tbl.c.class_oid == class_oid)
        .values(class_oid=None)
    )


def _get_names_stmt(oids: list[str]):
    return select(users_tbl.c.oid, users_tbl.c.name).where(
        users_tbl.c.oid.in_(oids)
    )


def _get_many_by_oids_stmt(oids: list[str]):
    return select(*_user_columns()).where(users_tbl.c.oid.in_(oids))


# Mongo ``{"past": {"$elemMatch": {"$regex": q, "$options": "i"}}}``: a
# case-insensitive regex over the ``past`` JSON array. Oracle 19c's SQL/JSON
# path does not support the ``like_regex ... flag "i"`` clause (21c+), so the
# array is serialised and matched with ``REGEXP_LIKE`` instead — same
# unescaped-regex, case-insensitive semantics.
_PAST_MATCH = "regexp_like(json_serialize(users.past), :past_value, 'i')"


def _all_users_stmt():
    return select(*_user_columns()).order_by(users_tbl.c.oid.asc())


def _list_admin_oids_stmt():
    """全部 admin 的 oid。

    复用 ``_ROLE_EXISTS``（JSON_EXISTS + 绑定参数）而不是
    ``DBMS_LOB.INSTR(roles, '"admin"')``：JSON_EXISTS 按数组元素精确匹配，
    不会把 ``"not_admin"`` 之类的子串误判成 admin。
    """
    return (
        select(users_tbl.c.oid)
        .where(text(_ROLE_EXISTS).bindparams(role_value="admin"))
        .order_by(users_tbl.c.oid.asc())
    )



def _list_by_id_prefix_stmt(prefix: str):
    return (
        select(*_user_columns())
        .where(func.regexp_like(users_tbl.c.business_id, "^" + prefix))
        .order_by(users_tbl.c.oid.asc())
    )


def _list_by_class_stmt(class_oid: str):
    return (
        select(*_user_columns())
        .where(users_tbl.c.class_oid == class_oid)
        .order_by(users_tbl.c.oid.asc())
    )


def _search_filter(query: str, logged_in: bool):
    if not logged_in:
        return [users_tbl.c.business_id == query]
    return [
        or_(
            func.regexp_like(users_tbl.c.name, query, "i"),
            func.regexp_like(users_tbl.c.business_id, query, "i"),
            text(_PAST_MATCH).bindparams(past_value=query),
        )
    ]


def _search_stmt(query: str, logged_in: bool):
    stmt = select(
        users_tbl.c.oid,
        users_tbl.c.name,
        users_tbl.c.business_id,
        users_tbl.c.class_oid,
    )
    conds = _search_filter(query, logged_in)
    if conds:
        stmt = stmt.where(*conds)
    return stmt.order_by(users_tbl.c.oid.asc())


def _search_count_stmt(query: str, logged_in: bool):
    stmt = select(func.count()).select_from(users_tbl)
    conds = _search_filter(query, logged_in)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _list_by_name_or_id_stmt(pattern: str):
    stmt = select(*_user_columns())
    if pattern:
        stmt = stmt.where(
            or_(
                func.regexp_like(users_tbl.c.name, pattern, "i"),
                func.regexp_like(users_tbl.c.business_id, pattern, "i"),
            )
        )
    return stmt.order_by(users_tbl.c.oid.asc())


def _group_by_id_prefix_stmt():
    prefix = func.substr(users_tbl.c.business_id, 1, 4)
    return (
        select(prefix.label("prefix"), func.json_arrayagg(users_tbl.c.oid).label("original_ids"))
        .group_by(prefix)
    )


# ---------------------------------------------------------------------------
# USER_TRASH（回收站）—— 硬删除前的整包归档
#
# DDL 在 ``db/oracle/ddl/V011__add_user_trash.sql``（**不自动执行**）。
# 这里刻意挂在**独立的 MetaData** 上：``db/ddl_check.py`` 比对
# ``db.models.metadata`` ↔ ``V001``，把回收站表塞进共享 metadata 会被它判成
# 「模型比 DDL 多一张表」的漂移。独立 metadata 让本表完全不影响那次比对。
#
# ``user_oid`` **故意没有外键**指向 ``users``：回收站的意义就是在 users 行被删掉
# 之后仍然保留整包数据；有外键会直接让删除失败（ORA-02292）。
# ---------------------------------------------------------------------------

_trash_metadata = MetaData()

user_trash_tbl = Table(
    "user_trash",
    _trash_metadata,
    Column("oid", VARCHAR2(24), primary_key=True),
    Column("user_oid", VARCHAR2(24), nullable=False),
    Column("deleted_by", VARCHAR2(24), nullable=True),
    Column("deleted_at", TIMESTAMP, nullable=False),
    Column("payload", CLOB, nullable=False),
    Column("restored_at", TIMESTAMP, nullable=True),
)

# ``files`` 的 BLOB（``content``）绝不进回收站 payload：CLOB 装不下附件字节，
# 而且字节本身没有「身份」语义。只抄元数据。
_FILES_METADATA_COLUMNS = (
    files_tbl.c.oid,
    files_tbl.c.owner_oid,
    files_tbl.c.kind,
    files_tbl.c.parent_oid,
    files_tbl.c.mime,
    files_tbl.c.size_bytes,
    files_tbl.c.width,
    files_tbl.c.height,
    files_tbl.c.sha256,
    files_tbl.c.original_name,
    files_tbl.c.purpose,
    files_tbl.c.entity_type,
    files_tbl.c.entity_oid,
    files_tbl.c.status,
    files_tbl.c.ocr_status,
    files_tbl.c.created_at,
    files_tbl.c.updated_at,
    files_tbl.c.deleted_at,
)

# 用户的所有关联行，以及**逐表处置决策**（v4.2.4-2 契约 §3.3A-3b 要求逐项列出）：
#
#   (名称, 表, 指向用户的列, 归档时读取的列(None=全部), 是否物理清理)
#
# * ``user_time`` / ``time_academic_year`` / ``time_with_origin`` /
#   ``user_device_bindings`` —— 真外键（NO ACTION，全库无 ON DELETE CASCADE），
#   不清理就删不掉 users 行；全量归档后物理删除。义工时长是**可以从活动台账
#   重算**的派生数据，且已随回收站整体保留，删除不丢事实。
# * ``activity_members`` —— 无外键的软引用，**只归档、不删**：这些行是别人活动
#   的参与记录/时长来源，删掉会静默改变他人的统计；保留后 ``member_oid`` 悬空，
#   与既有「未知用户」语义一致（``db/models.py`` 对 member_oid 的注释）。
# * ``files`` —— 无外键的软引用，**只归档元数据、不删也不置空**：置空会让附件
#   变成无主孤儿并破坏 ``ix_files_sha256`` 的 (owner_oid, sha256) 去重口径；
#   保留悬空 owner_oid 同样落到「未知用户」，且回收站行里存着 user_oid↔文件的
#   对应关系，可追溯。
#
# 未列入的软引用（``activities.creator_oid`` / ``appointee_oid``、
# ``imports.user_oid``）一律**保留不动**：前两列是 NOT NULL 的活动台账事实
# （改不了也不能改），后者是整批导入的日期范围审计记录，且带 BLOB 不适合进
# CLOB payload。
_CHILD_TABLES: tuple[tuple[str, Any, str, tuple[Any, ...] | None, bool], ...] = (
    ("user_time", user_time_tbl, "user_oid", None, True),
    ("time_academic_year", time_academic_year_tbl, "user_oid", None, True),
    ("time_with_origin", time_with_origin_tbl, "user_oid", None, True),
    ("user_device_bindings", user_device_bindings_tbl, "user_oid", None, True),
    ("activity_members", activity_members_tbl, "member_oid", None, False),
    ("files", files_tbl, "owner_oid", _FILES_METADATA_COLUMNS, False),
)


class UserGoneError(RuntimeError):
    """目标 users 行不存在——并发删除，或 id 在两次读写之间失效。"""


def _json_safe(value: Any) -> Any:
    """把一行里的列值压成可 JSON 序列化的形状。

    ``TIMESTAMP`` → ISO8601 字符串（payload 是 JSON，不是 Oracle 类型）；
    ``NUMBER`` 可能是 :class:`~decimal.Decimal`，转 ``float``；``JSONText`` 列
    （``roles`` / ``eligibility`` / ``past`` / ``time_with_origin.data``）已经是
    list/dict，**递归保形**——绝不能落进 ``str(value)`` 分支，否则一个 JSON 数组
    会被序列化成字符串 ``"['student']"``（读回来不再是数组）；BLOB 一律丢弃
    （``files.content`` 已在查询里排除，这里是纵深）。其余保持原样，认不出的类型
    退化成 ``str``：宁可留个字符串，也不让归档整体失败。
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    return str(value)


def _row_json(mapping: Any) -> Row:
    return {key: _json_safe(value) for key, value in mapping.items()}


def _child_select_stmt(
    table: Any, column: str, columns: tuple[Any, ...] | None, target: str
):
    projection = tuple(columns) if columns is not None else tuple(table.c)
    return select(*projection).where(table.c[column] == target)


def _purge_trash_stmt(cutoff: Any, batch_size: int):
    """一批回收站物理删除。

    ``ROWNUM`` 而非 ``FETCH FIRST n ROWS ONLY``：Oracle 的 ``DELETE`` 不接受后者
    （与 ``db/repos/oracle/app_logs.py::_purge_batch_stmt`` 同一写法）。
    只按 ``deleted_at`` 判过期——``restored_at`` 是标记位，不参与保留期计算。
    """
    return delete(user_trash_tbl).where(
        user_trash_tbl.c.deleted_at < cutoff,
        text("ROWNUM <= :batch_size").bindparams(batch_size=batch_size),
    )


def _count_expired_trash_stmt(cutoff: Any):
    return (
        select(func.count())
        .select_from(user_trash_tbl)
        .where(user_trash_tbl.c.deleted_at < cutoff)
    )


async def _existing_tables(session: AsyncSession, names: set[str]) -> set[str]:
    """本次连接的 schema 里真实存在的表名（小写）。

    生产 schema 可能与仓库 DDL 漂移（实测生产库缺 ``user_device_bindings``，
    而它是 ``_CHILD_TABLES`` 里的静态条目）。缺表时若照常查询会抛 ORA-00942，
    让整个「删除用户」失败，因此先按 ``user_tables`` 过滤，并把缺表名写进回收站
    payload（``skipped_tables``）留审计线索。
    """
    if not names:
        return set()
    stmt = text(
        "SELECT table_name FROM user_tables WHERE table_name IN ("
        + ",".join(f"'{name.upper()}'" for name in sorted(names))
        + ")"
    )
    rows = (await session.execute(stmt)).all()
    return {str(row[0]).lower() for row in rows}


async def archive_and_delete_user(
    session: AsyncSession, oid: str, deleted_by: str | None
) -> str:
    """在**调用方的事务里**归档并删除一个用户；返回回收站行的 oid。

    步骤（顺序是承重的）：
    1. 读 users 行（不存在 → :class:`UserGoneError`）；
    2. 读全部关联行（``_CHILD_TABLES``）拼成 payload；
    3. 写 ``user_trash``（先归档，后删除：删到一半失败也已经有完整快照）；
    4. 物理清理带外键的子表（``_CHILD_TABLES`` 里 cleanup=True 的那些）；
    5. 删 users 行，``rowcount`` 必须为 1，否则 :class:`UserGoneError`。

    本函数**自己不开事务、不 commit**：事务边界属于调用方
    （:meth:`OracleUsersRepo.delete_user_cascade` 或单测里的替身会话），因此
    任何一步抛异常都不会留下半删状态。
    """
    target = normalize_oid(oid)
    actor = normalize_oid(deleted_by) if is_valid_oid(deleted_by) else None

    user_row = (
        await session.execute(_get_by_oid_stmt(target))
    ).first()
    if user_row is None:
        raise UserGoneError(f"用户不存在: {target}")

    payload: dict[str, Any] = {
        "user": _row_json(user_row._mapping),
        "related": {},
    }
    all_names = {spec[0] for spec in _CHILD_TABLES}
    present = await _existing_tables(session, all_names)
    missing = sorted(all_names - present)
    if missing:
        # 记录缺表：本次没有归档/清理这些表，事后回溯时要能看出来。
        payload["skipped_tables"] = missing
    active = tuple(spec for spec in _CHILD_TABLES if spec[0] in present)
    for name, table, column, columns, _cleanup in active:
        rows = (
            await session.execute(_child_select_stmt(table, column, columns, target))
        ).all()
        payload["related"][name] = [_row_json(row._mapping) for row in rows]

    trash_oid = new_oid()
    await session.execute(
        insert(user_trash_tbl).values(
            oid=trash_oid,
            user_oid=target,
            deleted_by=actor,
            deleted_at=datetime.now(),
            payload=json.dumps(payload, ensure_ascii=False),
            restored_at=None,
        )
    )

    for _name, table, column, _columns, cleanup in active:
        if cleanup:
            await session.execute(delete(table).where(table.c[column] == target))

    removed = await session.execute(delete(users_tbl).where(users_tbl.c.oid == target))
    if int(removed.rowcount) != 1:
        raise UserGoneError(f"用户行未被删除（可能已被并发删除）: {target}")

    return trash_oid


def _page_row(mapping: Any) -> Row:
    """Aggregation-shaped row for ``list_page`` (matches users_v2 pipeline).
    ``has_password`` is an internal flag (``password_hash IS NOT NULL``): the
    ``pv``-less list endpoints must drop it, callers that only report password
    presence use it instead of re-fetching every row.
    """
    class_oid = mapping["class_oid"]
    class_name = mapping["class_name"]
    roles = mapping["roles"]
    return {
        "_id": mapping["oid"],
        "id": mapping["business_id"],
        "name": mapping["name"],
        "roles": roles if roles is not None else [],
        "class_id": class_oid if class_oid is not None else "",
        "class_name": class_name if class_name is not None else _UNKNOWN_CLASS,
        "banned": bool(mapping["banned"]),
        "has_password": mapping["password_hash"] is not None,
    }


def _banned_row(mapping: Any) -> Row:
    """当前被封用户的一行（``list_banned_page`` 的投影形状）。

    ``class_name`` 沿用 ``_page_row`` 的「未知班级」哨兵：班级被删/未分配时
    列表仍要给出一个可显示值，而不是让前端处理 ``None``。``ban_until`` 保持
    ``datetime | None`` 原样（``None`` = 永久），ISO 化是端点层的事。
    """
    class_name = mapping["class_name"]
    return {
        "_id": mapping["oid"],
        "id": mapping["business_id"],
        "name": mapping["name"],
        "class_name": class_name if class_name is not None else _UNKNOWN_CLASS,
        "ban_reason": mapping["ban_reason"],
        "ban_until": mapping["ban_until"],
        "ban_source": mapping["ban_source"],
        "ban_rule": mapping["ban_rule"],
    }


def _total_of(rows: Any) -> int | None:
    """The ``COUNT(*) OVER()`` value carried by a merged count+page result.

    ``None`` for an empty page: the window count is not observable then, and the
    caller decides whether the page is simply empty (``page == 1`` → ``0``) or
    out of range (a dedicated count statement is issued).
    """
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


def _user_doc(mapping: Any) -> Row:
    """Full document for ``get_by_oid`` / ``find_by_business_id``.

    ``password`` is always present (even when ``None``) because callers index it
    directly (``user["password"]``). Optional columns are only added when
    non-NULL so ``"eligibility" in user`` keeps its Mongo meaning.
    """
    doc: Row = {
        "_id": mapping["oid"],
        "id": mapping["business_id"],
        "name": mapping["name"],
        "password": mapping["password_hash"],
        "roles": mapping["roles"] if mapping["roles"] is not None else [],
    }
    if mapping["class_oid"] is not None:
        doc["class_id"] = mapping["class_oid"]
    if mapping["session_id"] is not None:
        doc["session_id"] = mapping["session_id"]
    if mapping["sex"] is not None:
        doc["sex"] = mapping["sex"]
    if mapping["eligibility"] is not None:
        doc["eligibility"] = mapping["eligibility"]
    if mapping["past"] is not None:
        doc["past"] = mapping["past"]
    if mapping["banned"]:
        doc["banned"] = True
    # 封禁元数据只补非 NULL 的：``"ban_until" in user`` 必须保持 Mongo 的
    # 「字段不存在」语义（永久封禁 = 该 key 不出现）。
    if mapping["ban_reason"] is not None:
        doc["ban_reason"] = mapping["ban_reason"]
    if mapping["ban_until"] is not None:
        doc["ban_until"] = mapping["ban_until"]
    if mapping["ban_source"] is not None:
        doc["ban_source"] = mapping["ban_source"]
    if mapping["ban_rule"] is not None:
        doc["ban_rule"] = mapping["ban_rule"]
    # 主设备同样只补非 NULL 的：``"primary_xuehai_id" in user`` 必须表示「已绑定」，
    # 未绑定是「字段不存在」（Oracle 把空串当 NULL，两者无法区分，只能看 key）。
    if mapping["primary_xuehai_id"] is not None:
        doc["primary_xuehai_id"] = mapping["primary_xuehai_id"]
    return doc


class OracleUsersRepo:
    """``UsersRepo`` over Oracle ADB (``zvms_new.users``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_by_oid(self, oid: str) -> Row | None:
        stmt = _get_by_oid_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        return _user_doc(row._mapping)

    async def find_by_business_id(self, business_id: str) -> Row | None:
        stmt = _find_by_business_id_stmt(business_id)
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        return _user_doc(row._mapping)

    async def list_page(
        self, query: str, role: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        page_stmt = _user_page_stmt(query, role, page, size)
        count_stmt = _user_count_stmt(query, role)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = _total_of(rows)
            if total is None:
                total = (
                    0
                    if page == 1
                    else int((await session.execute(count_stmt)).scalar_one())
                )
        return [_page_row(row._mapping) for row in rows], total

    async def list_banned_page(
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """当前被封禁（``banned = 1``）用户的一页，``(rows, total)``。

        「当前状态」以 ``USERS.BANNED`` 为准，不读 ``BAN_RECORDS``：流水是
        只增的历史账本（同一用户可能有多条、也可能被人工解封），拿它当列表
        会把已解封的人和重复封禁的次数一起显示出来。
        """
        page_stmt = _banned_page_stmt(query, page, size)
        count_stmt = _banned_count_stmt(query)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = _total_of(rows)
            if total is None:
                total = (
                    0
                    if page == 1
                    else int((await session.execute(count_stmt)).scalar_one())
                )
        return [_banned_row(row._mapping) for row in rows], total

    async def create(
        self,
        business_id: str,
        name: str,
        roles: list[str],
        password_hash: str | None,
        class_oid: str | None,
    ) -> str:
        oid = new_oid()
        class_value = normalize_oid(class_oid) if class_oid is not None else None
        stmt = _insert_user_stmt(
            oid, business_id, name, list(roles), password_hash, class_value
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def set_password(self, oid: str, password_hash: str | None) -> None:
        stmt = _set_password_stmt(normalize_oid(oid), password_hash)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_password_guarded(
        self, oid: str, password_hash: str, actor_oid: str
    ) -> int:
        """写密码的原子守卫版本：仅当「操作者即本人」或「目标非 admin」才落库。

        为什么用条件写：读角色与写密码分为两步时，目标可能在两步之间被并发
        改成 admin，于是前置判定放行了、写却落在了一个 admin 账号上。把角色
        判定与写入合成一条 SQL，数据库在一次原子执行里完成两者，窗口即消失。

        返回受影响行数：``0`` 表示守卫拒绝（未写入）。
        """
        stmt = _set_password_guarded_stmt(
            normalize_oid(oid), password_hash, normalize_oid(actor_oid)
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def set_roles(self, oid: str, roles: list[str]) -> None:
        stmt = _set_roles_stmt(normalize_oid(oid), list(roles))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_business_id(self, oid: str, business_id: str) -> None:
        """改学号（登录名）并轮换 ``session_id``（旧 token 立即失效）。

        唯一冲突由 ``uq_users_business_id`` 抛出 ``IntegrityError``，调用方
        （``users_v3_router``）转 409——这里不做「先查再写」的软判定，那是端点
        层的友好提示，约束才是最终裁决者。
        """
        stmt = _set_business_id_stmt(normalize_oid(oid), business_id)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def delete_user_cascade(self, oid: str, deleted_by: str) -> str:
        """硬删除用户：归档 → 清理子表 → 删 users 行，**一个事务**。

        默认（``self._session is None``）自己开一个 :func:`session_scope`：所有
        语句共用一次连接，最后一次 :meth:`commit`。任何一步抛异常都不会走到
        commit，``session_scope`` 关闭时会话自动回滚——因此**不存在半删状态**。
        注入了 session 时事务边界归调用方（同样只在调用方 commit 时才落库）。

        返回回收站行的 oid，供审计/回溯。目标行不存在（并发删除）时抛
        :class:`UserGoneError`，由端点转 404。
        """
        async with _session_for(self._session) as (session, owns):
            trash_oid = await archive_and_delete_user(session, oid, deleted_by)
            if owns:
                await session.commit()
        return trash_oid

    async def purge_trash_batch(self, cutoff: Any, batch_size: int) -> int:
        """物理删除一批 ``deleted_at < cutoff`` 的回收站行；返回删除行数。"""
        stmt = _purge_trash_stmt(cutoff, batch_size)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def count_expired_trash(self, cutoff: Any) -> int:
        """``deleted_at < cutoff`` 的回收站行数（过期判定与批删共用同一条件）。"""
        stmt = _count_expired_trash_stmt(cutoff)
        async with _session_for(self._session) as (session, _owns):
            return int((await session.execute(stmt)).scalar_one())

    async def set_banned(self, oid: str, banned: int) -> None:
        """兼容旧调用：``1`` → 管理员永久封禁，``0`` → 解封。

        旧接口没有原因/来源/到期时间的表达力，转发到 :meth:`set_ban`（
        ``reason=""`` / ``until=None`` / ``source="admin"``）与
        :meth:`clear_ban`，语义与 V013 之前一致，同时把元数据列写正。
        """
        if banned:
            await self.set_ban(oid, reason="", until=None, source="admin", rule_code=None)
        else:
            await self.clear_ban(oid)

    async def set_ban(
        self,
        oid: str,
        *,
        reason: str,
        until: datetime | None,
        source: str,
        rule_code: str | None,
    ) -> None:
        """封禁并记录原因/到期/来源（``banned = 1``），同一条写里轮换会话。"""
        stmt = _set_ban_stmt(normalize_oid(oid), reason, until, source, rule_code)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def clear_ban(self, oid: str) -> None:
        """解封并清空元数据（``banned = 0``），同一条写里轮换会话。"""
        stmt = _clear_ban_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_primary_xuehai(self, oid: str, xuehai_id: str) -> None:
        """绑定主学海设备（**不**轮换 ``session_id``，见语句构造器注释）。

        不做「已绑定时禁止覆盖」的判定：那是端点层的授权规则（只能从当前主设备
        发起），仓储只负责把值写进去。
        """
        stmt = _set_primary_xuehai_stmt(normalize_oid(oid), xuehai_id)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def clear_primary_xuehai(self, oid: str) -> None:
        """解绑主学海设备（列归 NULL → 回到未绑定、不做设备校验的状态）。"""
        stmt = _clear_primary_xuehai_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def list_banned_by_xuehai(self, xuehai_id: str) -> list[Row]:
        """主设备为该学海 ID 且 ``banned = 1`` 的用户文档。

        只按两列做原始筛选，**不**判 ``ban_until`` 是否过期：到期未解封的行
        ``banned`` 仍是 1，是否「此刻生效」必须由服务层用
        :func:`util.ban.ban_is_active` 判定（见 ``db/repos/oracle/users.py``
        的语句构造器注释）。返回完整文档，服务层据此拿 ``_id`` 与封禁元数据。
        """
        stmt = _list_banned_by_xuehai_stmt(xuehai_id)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def primary_owned_by_other(self, xuehai_id: str, user_oid: str) -> bool:
        """``xuehai_id`` 是否是**别人**的主设备。

        只回答「有没有另一个人把它当主设备」，不判封禁状态：调用方（临时密码
        连错封禁）要防的是「伪造受害者设备号借刀封人」，与对方此刻封没封无关。
        """
        stmt = _primary_owned_by_other_stmt(xuehai_id, normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return row is not None

    async def set_class(self, user_oid: str, class_oid: str | None) -> None:
        class_value = normalize_oid(class_oid) if class_oid is not None else None
        stmt = _set_class_stmt(normalize_oid(user_oid), class_value)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def detach_class(self, class_oid: str) -> int:
        stmt = _detach_class_stmt(normalize_oid(class_oid))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def add_past(self, oid: str, value: str) -> None:
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            current = (
                await session.execute(_get_past_stmt(target))
            ).scalar_one_or_none()
            merged = list(current) if current is not None else []
            merged.append(value)
            await session.execute(_set_past_stmt(target, merged))
            if owns:
                await session.commit()

    async def remove_past(self, oid: str, value: str) -> None:
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            current = (
                await session.execute(_get_past_stmt(target))
            ).scalar_one_or_none()
            if current is None:
                return
            remaining = [item for item in current if item != value]
            if remaining == list(current):
                return
            await session.execute(_set_past_stmt(target, remaining))
            if owns:
                await session.commit()

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(normalize_oid(oid) for oid in oids))
        if not unique:
            return {}
        stmt = _get_names_stmt(unique)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return {row._mapping["oid"]: row._mapping["name"] for row in rows}

    async def get_many_by_oids(self, oids: list[str]) -> dict[str, Row]:
        """Full user documents for many oids in one round trip.

        ``get_by_oid`` in a loop is the N+1 of every member/statistics list; this
        is the batch form. Unknown ids are simply absent from the result. The
        ``IN`` list is split into :data:`_OID_CHUNK`-sized statements because
        Oracle rejects more than 1000 elements in one ``IN`` (ORA-01795).
        """
        unique = list(dict.fromkeys(normalize_oid(oid) for oid in oids))
        if not unique:
            return {}
        found: dict[str, Row] = {}
        async with _session_for(self._session) as (session, _owns):
            for start in range(0, len(unique), _OID_CHUNK):
                chunk = unique[start : start + _OID_CHUNK]
                rows = (await session.execute(_get_many_by_oids_stmt(chunk))).all()
                for row in rows:
                    doc = _user_doc(row._mapping)
                    found[str(doc["_id"])] = doc
        return found

    async def list_all(self) -> list[Row]:
        stmt = _all_users_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def list_admin_oids(self) -> list[str]:
        """全部 admin 的 oid（双人授权用来找「另一位管理员」）。"""
        stmt = _list_admin_oids_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [str(row[0]) for row in rows]

    async def insert_dual_auth_use(
        self,
        approver_oid: str,
        otp_counter: int,
        action: str,
        operator_oid: str,
    ) -> str:
        """落一条动态码使用记录，返回新行 oid。

        唯一约束 ``uq_dual_approver_counter`` 是防重放的**唯一**裁决者，这里
        不做「先查后插」：并发两个请求会在同一个窗口里双双查到「未使用」。
        冲突（ORA-00001）由 SQLAlchemy 包成 ``IntegrityError`` 抛出，翻译成
        409 是守卫层的职责，本方法只负责让异常上抛。
        """
        oid = new_oid()
        stmt = insert(dual_auth_uses_tbl).values(
            oid=oid,
            approver_oid=normalize_oid(approver_oid),
            otp_counter=int(otp_counter),
            action=action,
            operator_oid=normalize_oid(operator_oid),
            created_at=datetime.now(),
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def list_by_id_prefix(self, prefix: str) -> list[Row]:
        stmt = _list_by_id_prefix_stmt(prefix)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def list_by_class(self, class_oid: str) -> list[Row]:
        stmt = _list_by_class_stmt(normalize_oid(class_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def search(self, query: str, logged_in: bool) -> tuple[list[Row], int]:
        page_stmt = _search_stmt(query, logged_in)
        count_stmt = _search_count_stmt(query, logged_in)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        out: list[Row] = []
        for row in rows:
            m = row._mapping
            out.append(
                {
                    "_id": m["oid"],
                    "name": m["name"],
                    "id": m["business_id"],
                    "class_id": m["class_oid"] if m["class_oid"] is not None else None,
                }
            )
        return out, int(total)

    async def list_by_name_or_id(self, pattern: str) -> list[Row]:
        stmt = _list_by_name_or_id_stmt(pattern)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def group_oids_by_id_prefix(self) -> list[Row]:
        stmt = _group_by_id_prefix_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        out: list[Row] = []
        for row in rows:
            m = row._mapping
            raw = m["original_ids"]
            if isinstance(raw, str):
                ids = json.loads(raw)
            else:
                ids = list(raw) if raw is not None else []
            out.append({"_id": m["prefix"], "originalIds": [str(x) for x in ids]})
        return out


__all__ = ["OracleUsersRepo", "UserGoneError", "archive_and_delete_user", "user_trash_tbl"]
