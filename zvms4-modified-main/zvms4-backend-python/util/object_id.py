from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from util.positions import trans_permissions
from typings.user import UserPosition as UserPositionV1
from typings.user_v2 import UserPosition as UserPositionV2
import jwt
from typing import Optional
from datetime import datetime, timezone
from db import backends
from db.ids import normalize_oid, is_valid_oid
from util.cert import jwt_decode
from util.group import get_user_permissions

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


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


async def get_user(oid: str):
    """
    Get user by oid
    """
    validate_object_id(oid)
    return await backends.get_users_repo().get_by_oid(oid)


async def compulsory_temporary_token(token: str = Depends(oauth2_scheme)):
    result = await _authenticate(token, "short", True)
    return result


async def password_change_token(token: str = Depends(oauth2_scheme)):
    return await _authenticate(token, "password_change", True)


async def authenticated_log_user(token: str = Depends(oauth2_scheme)):
    # Route dependencies enforce the required scope; logging accepts either
    # credential so a second auth dependency cannot break verification routes.
    return await _authenticate(token, "any", True)


async def optional_current_user(token: str = Depends(oauth2_scheme)):
    result = await _authenticate(token, "long", False)
    return result


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)):
    return await _authenticate(token, "long", True)


async def _authenticate(
    token: Optional[str],
    scope: str,
    exception: bool = True,
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
        current = await backends.get_users_repo().get_by_oid(oid)
        if current is None or current.get("banned"):
            return raise_exception()
        if payload.get("sid") != current.get("session_id", ""):
            return raise_exception()
        permissions = await get_user_permissions(current)
        user = {
            "id": oid,
            "perm": upgrade_user_positions(permissions),
            "per": permissions,
            "scope": token_scope,
            "purpose": purpose,
        }
        return user
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
    try:
        user = await _authenticate(bearer_token(raw), "long", False)
    except Exception:
        return False
    return bool(user) and "admin" in (user.get("per") or [])


def timestamp_change(date_string: str):
    """
    Change ISO-8601 to timestamp
    """
    dt = datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%S.%fZ")
    # Change the time zone to UTC
    dt = dt.replace(tzinfo=timezone.utc)
    # Get the timestamp
    timestamp = dt.timestamp()
    # Return the timestamp
    return int(timestamp)
