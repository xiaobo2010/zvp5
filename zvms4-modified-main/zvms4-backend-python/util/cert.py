from typing import Optional
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
import secrets
import string
import struct
from bcrypt import checkpw, gensalt, hashpw
from Crypto.Hash import SHA256

from db import backends
from db.ids import new_oid
from util.group import get_user_permissions


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
jwt_private_key = open("aes_key.txt", "r").read()


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
):
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


async def validate_by_cert(
    id: str, cert: str, type: Optional[str] = "long"
) -> tuple[str, bool]:
    """
    Authenticate a login credential.

    Returns `(token, weak_password)`. When the proven password is weak
    (initial state, or failing the strength rule) and a long-term token is
    requested, only a 5-minute temporary token is issued so the client is
    forced through the password-change loop; the `weak_password` flag lets
    the frontend react accordingly. Short-term requests stay allowed so
    the change loop itself is never blocked. TOTP logins are not affected
    (no password is involved).
    """
    auth_field = _read_credential(cert)
    user = await backends.get_users_repo().get_by_oid(id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("banned"):
        raise HTTPException(status_code=403, detail="User is banned")
    # 旧行为：``password`` 为 NULL 时把「初始密码 == 用户自己的学号」当作凭据
    # 比对。学号是公开可枚举的，任何能清空他人密码的人都能据此冒充对方登录
    # （双人授权被人格冒用击穿）。删除此回落后，未设置密码的账号无法用密码
    # 登录，只能走下方 TOTP 分支；最终 ``403 "Password incorrect"`` 的行为
    # 保持不变，登录端点仍维持「用户不存在」与「密码错误」不可区分。
    if user["password"] is None:
        authenticated = False
    else:
        authenticated = check_password(auth_field["password"], user["password"])
    if authenticated:
        if "eligibility" in user:
            eligibility = user["eligibility"]
        else:
            eligibility = []
        weak = user["password"] is None or pwd_strength(auth_field["password"]) < 3
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
    return hashpw(bytes(password, "utf-8"), gensalt()).decode("utf-8")


async def checkpwd(id: str, pwd: str):
    user = await backends.get_users_repo().get_by_oid(id)
    if user is not None and user.get("password") and check_password(
        pwd, user["password"]
    ):
        return True
    return False
