from typing import Optional
import bcrypt
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from fastapi import HTTPException
import jwt
import json
import datetime
import hashlib
import hmac
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
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


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


OTP_STEP = 30
OTP_DIGITS = 6


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


def verify_totp(user_id: str, code: str) -> bool:
    counter = int(datetime.datetime.now().timestamp() // OTP_STEP)
    secret = _totp_secret(user_id)
    return any(
        hmac.compare_digest(_totp_code(secret, counter - offset), code)
        for offset in range(2)
    )


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
        "exp": datetime.datetime.utcnow() + duration,
        "iat": datetime.datetime.utcnow(),
        "sub": id,
        "scope": scope,
        "per": permissions,
        "elg": eligibility,
        "jti": new_oid(),
    }
    result = jwt.encode(payload, jwt_private_key, algorithm="HS256")
    return result


def jwt_decode(token):
    return jwt.decode(token, jwt_private_key, algorithms=["HS256"], verify=True)


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
    auth_field = json.loads(rsa_decrypt(cert))
    time = auth_field["time"]
    # in a minute
    if time < datetime.datetime.now().timestamp() - 60:
        raise HTTPException(status_code=401, detail="Token expired")
    user = await backends.get_users_repo().get_by_oid(id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("banned"):
        raise HTTPException(status_code=403, detail="User is banned")
    if user["password"] is None:
        authenticated = auth_field["password"] == user["id"]
    else:
        authenticated = checkpw(
            bytes(auth_field["password"], "utf-8"), bytes(user["password"], "utf-8")
        )
    if authenticated:
        if "eligibility" in user:
            eligibility = user["eligibility"]
        else:
            eligibility = []
        weak = user["password"] is None or pwd_strength(auth_field["password"]) < 3
        if weak and type == "long":
            return (
                jwt_encode(id, eligibility, await get_user_permissions(user), "short"),
                True,
            )
        return jwt_encode(id, eligibility, await get_user_permissions(user), type=type), weak
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
                ),
                False,
            )
        raise HTTPException(status_code=403, detail="动态码错误或已过期")
    raise HTTPException(status_code=403, detail="Password incorrect")


async def get_hashed_password_by_cert(cert: str):
    auth_field = json.loads(rsa_decrypt(cert))
    time = auth_field["time"]
    # in a minute
    if time < datetime.datetime.now().timestamp() - 60:
        raise HTTPException(status_code=401, detail="Token expired")
    password = auth_field["password"]
    return hashpw(bytes(password, "utf-8"), gensalt()).decode("utf-8")


async def checkpwd(id: str, pwd: str):
    user = await backends.get_users_repo().get_by_oid(id)
    if checkpw(bytes(pwd, "utf-8"), bytes(user["password"], "utf-8")):
        return True
    return False
