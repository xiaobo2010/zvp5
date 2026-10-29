from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Optional
import asyncio
import bcrypt
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from fastapi import HTTPException
import jwt
import json
import math
import datetime
import hashlib
import hmac
import logging
import os
import secrets
import stat
import string
import struct
from Crypto.Hash import SHA256

from db import backends
from db.ids import new_oid
from util.ban import ban_is_active, raise_banned
from util.group import get_user_permissions

PLATFORM_KEY_PATH = "aes_key.txt"

_logger = logging.getLogger(__name__)


class Auth:
    password: str
    time: int


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())


def check_password(password: str, hashed: str):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        # bcrypt rejects inputs longer than its 72-byte limit. A malformed
        # credential is a failed check, never an internal server error.
        return False


async def hash_password_async(password) -> bytes:
    """``hash_password`` 的线程池版本，给 ``async def`` 调用方用。

    bcrypt cost 12 单次约 250ms 且是纯 CPU：直接在协程里调会把整个事件
    循环按死这么久，一次并发登录就能让所有请求排队。cost 保持 12，只换
    执行位置。
    """
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, hashed: str) -> bool:
    """``check_password`` 的线程池版本；判定口径与原函数逐字相同。"""
    return await asyncio.to_thread(check_password, password, hashed)


def _custom_password_flags(rows) -> list[bool]:
    flags = []
    for row in rows:
        stored = row.get("password")
        flags.append(stored is not None and not check_password(row["id"], stored))
    return flags


async def custom_password_flags_async(rows) -> list[bool]:
    """``pwdm`` 页内判定：True = 库里存的是自定义密码（不等于学号）。

    整页一次性交给线程：``perpage`` 上限是 100，逐人 to_thread 只会多付
    100 次线程池往返，bcrypt 的 CPU 一秒都省不下来，还把循环切得稀碎。
    """
    return await asyncio.to_thread(_custom_password_flags, rows)


def pwd_strength(password: str) -> int:
    """
    Mirror of the frontend strength rule (stores/user.ts
    `validatePasswordStrength`): 8-14 printable ASCII characters with at
    least 3 of the 4 character classes (lower / upper / digit / symbol).
    """
    if not (8 <= len(password) <= 14):
        return 0
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in password):
        return 0
    strength = 0
    if any("a" <= ch <= "z" for ch in password):
        strength += 1
    if any("A" <= ch <= "Z" for ch in password):
        strength += 1
    if any(ch.isdigit() for ch in password):
        strength += 1
    if any(
        not ("a" <= ch <= "z" or "A" <= ch <= "Z" or ch.isdigit())
        for ch in password
    ):
        strength += 1
    return strength


public_key = RSA.import_key(open("rsa_public_key.pem", "rb").read())
private_key = RSA.import_key(open("rsa_private_key.pem", "rb").read())


def read_platform_key(path: str = PLATFORM_KEY_PATH) -> str:
    """读平台密钥（JWT / TOTP 的派生源），顺手收紧过宽的权限位。

    组/其他可读意味着同机任何账号都能签出合法 token，等于全站身份可伪造。
    发现就 chmod 0600 并告警；收紧失败也只告警——密钥读不到就没法启动，
    权限问题却可以留给运维处理。
    """
    try:
        mode = os.stat(path).st_mode
    except OSError:
        mode = 0
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        try:
            os.chmod(path, 0o600)
            _logger.warning("密钥文件 %s 权限过宽，已收紧为 0600", path)
        except OSError as exc:
            _logger.warning("密钥文件 %s 权限过宽且无法收紧：%s", path, exc)
    with open(path, "r") as handle:
        return handle.read()


jwt_private_key = read_platform_key()


def rsa_encrypt(plaintext):
    cipher = PKCS1_OAEP.new(public_key, hashAlgo=SHA256)
    encrypt_text = cipher.encrypt(bytes(plaintext.encode("utf8")))
    return encrypt_text.hex()


def rsa_decrypt(ciphertext):
    cipher = PKCS1_OAEP.new(private_key, hashAlgo=SHA256)
    decrypt_text = cipher.decrypt(bytes.fromhex(ciphertext))
    return decrypt_text.decode("utf8")


OTP_STEP = 60
OTP_DIGITS = 6


def generate_initial_password() -> str:
    """
    生成一个**一次性初始密码**（12 位小写字母 + 数字，约 62 bit 熵）。

    刻意让它不达 ``pwd_strength`` 的强度规则：这样新建/重置后的凭据走
    ``validate_by_cert`` 既有的「弱密码 → 只发 5 分钟临时 token → 前端强制改密」
    闭环，用户必须自己设定强密码。随机性保证它不可猜测——与旧的「初始密码 ==
    学号」有本质区别：学号公开可枚举，等于人人可登录。

    若将来强度规则变化导致它意外达标，这里直接抛错（而不是静默失去强制改密）。
    """
    alphabet = string.ascii_lowercase + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(12))
    if pwd_strength(password) >= 3:
        raise RuntimeError(
            "一次性初始密码意外达到了强度要求，会跳过强制改密闭环"
        )
    return password


def _totp_secret(user_id: str) -> bytes:
    return hmac.new(
        jwt_private_key.encode("utf-8"),
        f"totp:{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _totp_code(secret: bytes, counter: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % (10 ** OTP_DIGITS):0{OTP_DIGITS}d}"


def get_current_totp(user_id: str) -> tuple[str, float]:
    now = datetime.datetime.now().timestamp()
    counter = int(now // OTP_STEP)
    code = _totp_code(_totp_secret(user_id), counter)
    return code, (counter + 1) * OTP_STEP


def match_totp_counter(user_id: str, code: str) -> Optional[int]:
    """返回命中的计数器窗口；不命中返回 ``None``。

    只比对 ``counter`` 与 ``counter-1`` 两个窗口，与 :func:`verify_totp` 原有
    语义完全一致（容忍一个 ``OTP_STEP`` 的边界漂移）。返回计数器本身是为了让
    双人授权把它当**唯一防重放键**：(approver, counter) 一次一用，比「码字符串」
    更稳（同一个 6 位码可能在不同窗口重复出现）。
    """
    counter = int(datetime.datetime.now().timestamp() // OTP_STEP)
    secret = _totp_secret(user_id)
    for offset in range(2):
        candidate = counter - offset
        if hmac.compare_digest(_totp_code(secret, candidate), code):
            return candidate
    return None


def verify_totp(user_id: str, code: str) -> bool:
    return match_totp_counter(user_id, code) is not None


def used_otp_credential(cert: str, user_id: str) -> bool:
    try:
        auth_field = json.loads(rsa_decrypt(cert))
        password = auth_field.get("password", "")
        return (
            password.startswith("*")
            and password.endswith("*")
            and verify_totp(user_id, password[1:-1])
        )
    except Exception:
        return False


def jwt_encode(
    id: str,
    eligibility: list[str],
    permissions: list[str],
    type: Optional[str] = "long",
    *,
    session_id: str = "",
    purpose: str = "verify",
    expires_in: Optional[datetime.timedelta] = None,
    tp: bool = False,
):
    """签发 JWT。

    ``expires_in`` 覆盖默认有效期但**不改 scope**：临时密码登入要的是「1 小时的
    正式会话」，不是「5 分钟的改密授权」，后者会让用户刚进来就被要求改密。
    ``tp`` 标记「本会话由临时密码产生」，下游据此把日志级别抬到 warning——
    这条链路本身就是安全事件，不该混在 info 里。
    """
    if type == "long":
        duration = datetime.timedelta(days=10)
        scope = "access_token"
    elif type == "otp":
        # Dynamic-code logins get a shorter-lived session than password logins.
        duration = datetime.timedelta(days=1)
        scope = "access_token"
    else:
        # Dangerous Zone Access needs temporary token, others need access token.
        duration = datetime.timedelta(minutes=5)
        scope = "temporary_token"
    if expires_in is not None:
        duration = expires_in
    payload = {
        "iss": "zvms",
        "exp": datetime.datetime.now(datetime.timezone.utc) + duration,
        "iat": datetime.datetime.now(datetime.timezone.utc),
        "sub": id,
        "scope": scope,
        "per": permissions,
        "elg": eligibility,
        "jti": new_oid(),
        "sid": session_id,
        "purpose": purpose,
    }
    if tp:
        payload["tp"] = True
    result = jwt.encode(payload, jwt_private_key, algorithm="HS256")
    return result


def jwt_decode(token):
    return jwt.decode(
        token,
        jwt_private_key,
        algorithms=["HS256"],
        issuer="zvms",
        options={"require": ["iss", "exp", "iat", "sub", "scope", "per", "jti", "sid"]},
    )


def _credential_timestamp(value) -> float:
    """Normalize frontend milliseconds and legacy seconds to UTC seconds."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid credential timestamp")
    # JavaScript Date.now() is milliseconds; older clients sent seconds.
    if not math.isfinite(timestamp) or isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Invalid credential timestamp")
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    if timestamp < now - 60 or timestamp > now + 60:
        raise HTTPException(status_code=401, detail="Token expired")
    return timestamp


def _read_credential(cert: str) -> dict:
    try:
        credential = json.loads(rsa_decrypt(cert))
        if not isinstance(credential, dict) or not isinstance(credential.get("password"), str):
            raise ValueError("Malformed credential")
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(status_code=400, detail="Invalid credential")
    _credential_timestamp(credential.get("time"))
    return credential


# 登录端点在同一个请求里往往已经读过这一行 users（按学号查，或按 oid 查），
# 而 ``validate_by_cert`` 自己还会再读一次同一行。跨洋链路单程 138ms，这一次
# 重复读是白付的，所以让调用方把「刚读到的文档」放进来复用。
#
# 用 ContextVar 而不是给 ``validate_by_cert`` 加参数：它的调用签名是稳定契约
# （多处单测按 ``(id, credential, mode)`` 三参数打桩），加位置/关键字参数都会
# 把「省一次往返」变成破坏性变更。没设置或 oid 对不上时行为与原来完全一致——
# 自己查库，所以直连调用方不受影响。
_prefetched_user: ContextVar[Optional[tuple[str, Optional[dict]]]] = ContextVar(
    "cert_prefetched_user", default=None
)


@asynccontextmanager
async def prefetched_user(user_oid: str, user_doc: Optional[dict]):
    """在此上下文内让 ``validate_by_cert`` 复用 ``user_doc``。

    ``user_doc`` 允许是 ``None``：那表示调用方刚刚确认过「这个 oid 查不到」，
    同样不必再读一次。退出时无条件复位，避免值泄漏到同进程的其他请求。
    """
    token = _prefetched_user.set((str(user_oid), user_doc))
    try:
        yield
    finally:
        _prefetched_user.reset(token)


async def validate_by_cert(
    id: str, cert: str, type: Optional[str] = "long"
) -> tuple[str, bool]:
    """
    Authenticate a login credential.

    Returns `(token, weak_password)`. When the proven password is weak
    (initial state, failing the strength rule, or equal to the account's student
    number — the initial password) and a long-term token is
    requested, only a 5-minute temporary token is issued so the client is
    forced through the password-change loop; the `weak_password` flag lets
    the frontend react accordingly. Short-term requests stay allowed so
    the change loop itself is never blocked. TOTP logins are not affected
    (no password is involved).
    """
    auth_field = _read_credential(cert)
    prefetched = _prefetched_user.get()
    if prefetched is not None and prefetched[0] == str(id):
        # 调用方刚读到的就是这一行（含「确实不存在」），别再花一次往返。
        user = prefetched[1]
    else:
        user = await backends.get_users_repo().get_by_oid(id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # 登录前的封禁闸：到期封禁（banned 仍为 1）必须放行，否则刑满用户
    # 永远无法重新登录——判定口径统一交给 util.ban。
    if ban_is_active(user):
        raise_banned(user)
    # 旧行为：``password`` 为 NULL 时把「初始密码 == 用户自己的学号」当作凭据
    # 比对。学号是公开可枚举的，任何能清空他人密码的人都能据此冒充对方登录
    # （双人授权被人格冒用击穿）。删除此回落后，未设置密码的账号无法用密码
    # 登录，只能走下方 TOTP 分支；最终 ``403 "Password incorrect"`` 的行为
    # 保持不变，登录端点仍维持「用户不存在」与「密码错误」不可区分。
    if user["password"] is None:
        authenticated = False
    else:
        authenticated = await verify_password_async(
            auth_field["password"], user["password"]
        )
    if authenticated:
        if "eligibility" in user:
            eligibility = user["eligibility"]
        else:
            eligibility = []
        # 「密码就是学号」（建号初始密码）→ 无条件走强制改密，不看 pwd_strength：
        # 学号规则放宽后班级可能取到 8-14 位、≥3 字符类的值（如 ``Abc12345``），
        # 形状判定会把它当强密码，从而跳过强制改密。旧规则下 8 位纯数字恒为 1 类、
        # 必然 <3，所以这条前置判定只是把旧语义显式化，对任意学号形状都成立。
        # 精确字符串比较即可：business_id 落库前已 strip。
        password_is_student_id = auth_field["password"] == user.get("id")
        weak = (
            user["password"] is None
            or password_is_student_id
            or pwd_strength(auth_field["password"]) < 3
        )
        if weak:
            return (
                jwt_encode(id, eligibility, await get_user_permissions(user), "short",
                           session_id=user.get("session_id", ""), purpose="password_change"),
                True,
            )
        return jwt_encode(id, eligibility, await get_user_permissions(user), type=type,
                          session_id=user.get("session_id", "")), weak
    raw_password = auth_field["password"]
    if raw_password.startswith("*") and raw_password.endswith("*"):
        if verify_totp(id, raw_password[1:-1]):
            # Dynamic-code (TOTP) login: the code is computed locally per
            # user and is never delivered through any external channel.
            return (
                jwt_encode(
                    id,
                    user.get("eligibility", []),
                    await get_user_permissions(user),
                    type="otp" if type == "long" else type,
                    session_id=user.get("session_id", ""),
                ),
                False,
            )
        raise HTTPException(status_code=403, detail="动态码错误或已过期")
    raise HTTPException(status_code=403, detail="Password incorrect")


async def get_hashed_password_by_cert(cert: str):
    auth_field = _read_credential(cert)
    password = auth_field["password"]
    if pwd_strength(password) < 3:
        raise HTTPException(status_code=400, detail="密码强度不足")
    return (await hash_password_async(password)).decode("utf-8")


async def checkpwd(id: str, pwd: str):
    user = await backends.get_users_repo().get_by_oid(id)
    if user is not None and user.get("password") and await verify_password_async(
        pwd, user["password"]
    ):
        return True
    return False
