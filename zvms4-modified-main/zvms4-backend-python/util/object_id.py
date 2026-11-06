from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from sqlalchemy.exc import DatabaseError, InterfaceError, OperationalError

from util.positions import trans_permissions
from typings.user import UserPosition as UserPositionV1
from typings.user_v2 import UserPosition as UserPositionV2
import jwt
from contextvars import ContextVar
from typing import Optional
from datetime import datetime, timezone
from db import backends
from db.ids import normalize_oid, is_valid_oid
from util.ban import ban_is_active, raise_banned
from util.cert import jwt_decode
from util.group import get_user_permissions

try:  # 驱动层异常可能没被 SQLAlchemy 包成 DBAPIError（如连接中途断开）
    import oracledb

    _DRIVER_ERRORS: tuple[type[BaseException], ...] = (oracledb.Error,)
except ImportError:  # pragma: no cover - oracledb 是硬依赖，仅为导入路径兜底
    _DRIVER_ERRORS = ()

# 数据库不可用 ≠ 凭据无效：401/403 会让前端立刻退到登录页，把一次短暂的
# DB 抖动伪装成「登录过期」。503 才是可重试的事实。
DB_UNAVAILABLE_DETAIL = "数据库暂时不可用，请稍后重试"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

# 请求级日志级别下限：临时密码登入产生的会话里，所有 info 日志都按 warning 落库。
# 用 ContextVar 而不是「按 user 查库」：日志写入是同步热点，而「本次请求是谁」
# 在认证阶段就已经知道了，再查一次库只会多一次 IO、还可能读到不一致的快照。
log_level_floor: ContextVar[Optional[str]] = ContextVar(
    "zvms_log_level_floor", default=None
)


async def _log_level_scope():
    """请求结束时把级别下限复位。

    做成依赖生成器（而不是在 ``_authenticate`` 里 try/finally）是因为下限必须
    **贯穿整个 handler**：日志是在 handler 里写的，认证函数返回时就清掉等于
    什么都没做。捕获进入时的值再恢复，避免嵌套依赖之间互相污染。
    """
    previous = log_level_floor.get()
    try:
        yield
    finally:
        log_level_floor.set(previous)


def validate_object_id(id: str):
    try:
        return normalize_oid(id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Object ID")


def string_to_option_object_id(id: str):
    if is_valid_oid(id):
        return normalize_oid(id)
    return None


def upgrade_user_positions(positions: list[UserPositionV1]) -> list[UserPositionV2]:
    return [trans_permissions(pos) for pos in positions]


async def compulsory_temporary_token(
    token: str = Depends(oauth2_scheme), request: Request = None
):
    result = await _authenticate(token, "short", True, request)
    return result


async def password_change_token(
    token: str = Depends(oauth2_scheme), request: Request = None
):
    return await _authenticate(token, "password_change", True, request)


async def authenticated_log_user(
    token: str = Depends(oauth2_scheme),
    _log_scope=Depends(_log_level_scope),
    request: Request = None,
):
    # Route dependencies enforce the required scope; logging accepts either
    # credential so a second auth dependency cannot break verification routes.
    return await _authenticate(token, "any", True, request)


async def optional_current_user(
    token: str = Depends(oauth2_scheme),
    _log_scope=Depends(_log_level_scope),
    request: Request = None,
):
    result = await _authenticate(token, "long", False, request)
    return result


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    _log_scope=Depends(_log_level_scope),
    request: Request = None,
):
    return await _authenticate(token, "long", True, request)


_AUTH_CACHE_ATTR = "_zvms_request_auth"


def _auth_cache(request: Optional[Request]) -> Optional[dict]:
    """Per-request token -> resolved user cache.

    A write endpoint depends on ``get_current_user`` *and* ``inject_log``, which
    lives in a different module and therefore cannot share FastAPI's dependency
    cache. Both then ran a full authentication (user read + permissions read)
    for the same request. The cache is request-scoped state, so nothing survives
    the response.
    """
    if request is None:
        return None
    cache = getattr(request.state, _AUTH_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(request.state, _AUTH_CACHE_ATTR, cache)
    return cache


def _scope_satisfied(token_scope: str, purpose, scope: str) -> bool:
    if token_scope not in ("access_token", "temporary_token"):
        return False
    if scope == "long" and token_scope != "access_token":
        return False
    if scope in ("short", "password_change") and token_scope != "temporary_token":
        return False
    if purpose == "password_change" and scope not in ("password_change", "any"):
        return False
    return True


async def _read_auth_user(oid: str):
    """认证用的用户读：优先窄投影 ``get_for_auth``，没有该方法退回整行读。

    ``db.repos.protocols`` 只声明 ``get_by_oid``（``get_for_auth`` 是
    Oracle 实现多出来的能力），测试里的内存仓储同样只实现 ``get_by_oid``；
    两处都退化成原路径，语义不变，只是少了 ``past`` / ``eligibility``。
    """
    repo = backends.get_users_repo()
    reader = getattr(repo, "get_for_auth", None)
    if reader is None:
        return await repo.get_by_oid(oid)
    return await reader(oid)


PASSWORD_VERIFICATION_REQUIRED = "需要密码验证后才能继续"


async def require_password_verification(
    verification_token: Optional[str],
    user: dict,
    *,
    detail: str = PASSWORD_VERIFICATION_REQUIRED,
) -> dict:
    """高危操作的「密码再认证」闸：必须是一张**本人**的 ``purpose=verify`` 短令牌。

    令牌由 ``POST /api/users/auth``（purpose='verify'）用密码换出，5 分钟有效。
    这里沿用 ``_authenticate`` 的既有一切判定（签名、过期、scope、封禁、sid），
    只把「缺令牌 / 无效 / 过期 / 伪造 / 不是本人」统一翻成 **403**：

    前端 axios 拦截器把 401 当作「登录过期」，会清会话并跳登录页——密码框里打错
    一次就把用户踢出去，属于明显的错误语义。403 只表示「这次动作缺一次复核」。

    ``exception=False`` 让 ``_authenticate`` 返回 ``None`` 而不是抛 401；库不可用时
    它仍会抛 503，那是完全不同的事实，不能被吞成 403。
    """
    verified = await _authenticate(verification_token, "short", False)
    if (
        verified is None
        or str(verified.get("id")) != str(user.get("id"))
        or verified.get("purpose") != "verify"
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return verified


async def _authenticate(
    token: Optional[str],
    scope: str,
    exception: bool = True,
    request: Optional[Request] = None,
):
    """
    Inject `Depends`, returning user info
    """

    def raise_exception():
        if exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            return None

    try:
        if token is None:
            return raise_exception()
        # Decode JWT
        payload = jwt_decode(token)
        oid: str = payload.get("sub", None)
        exp: int = payload.get("exp", None)
        jti: str = payload.get("jti", None)

        # Check if the token is valid
        if oid is None or exp is None or jti is None:
            return raise_exception()

        # Check if the token is expired
        if exp is not None and datetime.now(timezone.utc).timestamp() >= exp:
            return raise_exception()
        # A short/temporary credential is the explicit proof-of-identity gate.
        # Do not allow a missing or unknown scope to pass this stricter path.
        token_scope = payload.get("scope")
        if token_scope not in ("access_token", "temporary_token"):
            return raise_exception()
        if scope == "long" and token_scope != "access_token":
            return raise_exception()
        if scope in ("short", "password_change") and token_scope != "temporary_token":
            return raise_exception()
        purpose = payload.get("purpose")
        if purpose == "password_change" and scope not in ("password_change", "any"):
            return raise_exception()
        if not is_valid_oid(oid):
            return raise_exception()
        cache = _auth_cache(request)
        cached = cache.get(token) if (cache is not None and token) else None
        if cached is not None and _scope_satisfied(cached[1], cached[2], scope):
            if cached[0].get("temp_session"):
                log_level_floor.set("warning")
            return cached[0]
        current = await _read_auth_user(oid)
        if current is None:
            return raise_exception()
        # 封禁是与「凭据过期/被撤销」不同的事实，客户端必须能分辨：封禁会把
        # session_id 轮换掉，若这里统一返回 401，前端只能当成登录过期并立刻
        # 退到登录页，用户永远看不到封禁警告。故在 sid 校验之前用 403 + 稳定
        # 标记返回；detail 是机器可读契约，前端不得直接展示。
        # 判定必须走 ban_is_active：到期封禁的 banned 列不会自动归零，只看
        # ``banned`` 会让刑满用户永久进不来。
        if ban_is_active(current):
            if exception:
                raise_banned(current)
            return None
        if payload.get("sid") != current.get("session_id", ""):
            return raise_exception()
        permissions = await get_user_permissions(current)
        temp_session = bool(payload.get("tp"))
        user = {
            "id": oid,
            "perm": upgrade_user_positions(permissions),
            "per": permissions,
            "scope": token_scope,
            "purpose": purpose,
            # 临时密码登入的会话：显式的派生事实，让读路径不必自己解 token。
            "temp_session": temp_session,
            # Additive: the very document this request was authenticated
            # against already carries ``class_id``. Read paths that would
            # otherwise re-fetch the same user (e.g. the secretary branch of
            # ``validate_read_group_permission``) can reuse it. The key is
            # always present — ``None`` means "no class" — so consumers can
            # tell "already known" from "not supplied" (a hand-built dict in a
            # test or a direct call still falls back to a fresh read).
            "class_id": current.get("class_id"),
        }
        if temp_session:
            # 临时密码会话期间写下的所有日志都至少是 warning（typings.log 消费）。
            log_level_floor.set("warning")
        if cache is not None and token:
            cache[token] = (user, token_scope, purpose)
        return user
    except (OperationalError, DatabaseError, InterfaceError, *_DRIVER_ERRORS) as exc:
        # 认证要读库（用户 + 当前 roles）；库抖动必须翻成可重试的 503，
        # 不能冒泡成 500，也不能伪装成 401。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=DB_UNAVAILABLE_DETAIL,
        ) from exc
    except (jwt.PyJWTError, TypeError, ValueError):
        return raise_exception()


def bearer_token(raw) -> Optional[str]:
    """
    Pull the token out of an ``Authorization`` value (``Bearer <token>``) or a
    bare token. Anything unusable yields ``None``; never raises.
    """
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1", "ignore")
    value = str(raw).strip()
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return value or None


async def is_admin_token(raw) -> bool:
    """
    Maintenance-mode admission: the token goes through the same path as
    :func:`get_current_user` (``_authenticate`` with the ``long`` scope), so it
    enforces signature, expiry, scope/purpose, a valid oid, the user existing
    and being unbanned, and the ``sid`` ↔ ``session_id`` match; ``per`` is then
    derived from the *current* roles, not from the JWT snapshot.

    Never raises: a missing token, a bad signature, an expired token or any
    other failure all mean "not admin", so middleware callers can stay total.
    """
    previous = log_level_floor.get()
    try:
        user = await _authenticate(bearer_token(raw), "long", False)
    except Exception:
        return False
    finally:
        # 中间件这条路径不经依赖清理，自己把下限复位：维护判定之后日志照旧
        # 按调用方级别落库，不继承上一个请求留下的 warning 下限。
        log_level_floor.set(previous)
    return bool(user) and "admin" in (user.get("per") or [])
