"""OCR 引擎注册表的管理面（admin CRUD 的业务校验与组装）。

为什么独立成模块：``services.ai.ocr_engines`` 是**纯适配器**（只吃明文凭证、不碰
加解密、不碰库），把校验/加解密塞进去会让三个适配器的同签名口子变浑；而路由层又
不该写业务校验（错误码要稳定、加解密只该有一处）。中间这一层就是它。

三条硬口径：

* **凭证只以密文进库**：整份 secret dict 序列化后走 :func:`crypto.encrypt_secret`，
  与 ``ai_credentials.api_key_cipher`` 同一套 AES-GCM。明文不落库、不进日志、
  不出现在任何响应里（视图里只有 ``key_hint`` = 账号标识的末四位）。
* **必填字段按 kind 定**：缺 region 的腾讯引擎会在第一次识别时才炸，而那时用户
  已经扣过额度了——录入时就拒掉。
* **``rapidocr`` 不接受凭证**：本地引擎没有账号，收下 secret 只能意味着管理员把
  密钥贴错了地方。
"""

from __future__ import annotations

import json
import logging
import re

from db import backends
from db.repos.protocols import DuplicateOcrEngine
from services.ai import crypto, ocr_engines
from services.ai.routing import RouteError

_logger = logging.getLogger(__name__)

# 引擎名会进 ``ai_call_logs.provider``（VARCHAR2(40)）与前端下拉，所以按 ASCII
# 小写名收窄；上限取 40 而不是表上的 64：超 40 的名字会让审计写入静默失败
# （``audit.record`` 吞异常），等于丢掉对账依据。
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
_PRIORITY_MIN = 0
_PRIORITY_MAX = 999  # NUMBER(3)

# 每种 kind 的**必填**凭证字段；``endpoint`` 之类可选字段另算（见 _secret_dict）。
_REQUIRED_SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    ocr_engines.KIND_TENCENT: ("secret_id", "secret_key", "region"),
    ocr_engines.KIND_ALIYUN: ("access_key_id", "access_key_secret"),
    ocr_engines.KIND_RAPIDOCR: (),
}

# key_hint 取「账号标识」而不是密钥本身：给管理界面认「这是哪把 key」，
# 露末四位不构成泄露（真正的密钥一位都不露）。
_HINT_FIELD: dict[str, str] = {
    ocr_engines.KIND_TENCENT: "secret_id",
    ocr_engines.KIND_ALIYUN: "access_key_id",
}


def public_view(row: dict) -> dict:
    """对外视图：不含 ``secret_cipher``（只在 ``has_secret`` 里透露有没有凭证）。"""
    return {
        "oid": row.get("_id"),
        "name": row.get("name"),
        "kind": row.get("kind"),
        "config": dict(row.get("config") or {}),
        "key_hint": row.get("key_hint"),
        "has_secret": bool(row.get("secret_cipher")),
        "enabled": bool(row.get("enabled", True)),
        "priority": int(row.get("priority") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def list_engines() -> list[dict]:
    """admin 视图：连 disabled 一起给（停用不等于删除，运维要能看见）。"""
    rows = await backends.get_ai_ocr_engines_repo().list_all()
    return [public_view(row) for row in rows]


async def create_engine(
    *,
    name: object,
    kind: object,
    config: object = None,
    secret: object = None,
    enabled: bool = True,
    priority: object = 100,
) -> dict:
    clean_kind = _clean_kind(kind)
    clean_name = _clean_name(name)
    clean_config = _clean_config(config)
    cipher, hint = _seal_secret(clean_kind, secret)

    repo = backends.get_ai_ocr_engines_repo()
    try:
        oid = await repo.insert(
            name=clean_name,
            kind=clean_kind,
            config=clean_config,
            secret_cipher=cipher,
            key_hint=hint,
            enabled=bool(enabled),
            priority=_clean_priority(priority),
        )
    except DuplicateOcrEngine:
        raise _duplicate(clean_name) from None
    # 撞名也可能以「类名相同但来自另一层」的形式出现（仓储实现细节），按名字兜底。
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "DuplicateOcrEngine":
            raise _duplicate(clean_name) from None
        raise

    row = await repo.get_by_oid(oid)
    if row is None:  # pragma: no cover - 刚插入就查不到 = 仓储异常
        raise RouteError("AI_OCR_ENGINE_NOT_FOUND", "ocr engine not found", 404)
    _logger.info("ai ocr engine created: kind=%s name=%s", clean_kind, clean_name)
    return public_view(row)


async def update_engine(oid: str, fields: dict) -> dict:
    """部分更新：``enabled`` / ``priority`` / ``config`` / ``secret`` / ``name``。

    ``None`` = 不改（与仓储协议一致）。``kind`` 刻意**不可改**：换引擎类型本质上是
    换一份凭证与一套请求参数，删行重建比「原地改 kind 却保留旧 secret」清楚得多。
    """
    repo = backends.get_ai_ocr_engines_repo()
    row = await repo.get_by_oid(oid)
    if row is None:
        raise _not_found()

    kind = str(row.get("kind") or "")
    changes: dict = {}
    if fields.get("name") is not None:
        changes["name"] = _clean_name(fields["name"])
    if fields.get("config") is not None:
        changes["config"] = _clean_config(fields["config"])
    if fields.get("enabled") is not None:
        changes["enabled"] = bool(fields["enabled"])
    if fields.get("priority") is not None:
        changes["priority"] = _clean_priority(fields["priority"])
    if fields.get("secret") is not None:
        if not _REQUIRED_SECRET_FIELDS[kind]:
            raise RouteError(
                "AI_OCR_ENGINE_INVALID", f"{kind} engine takes no credentials", 400
            )
        # 仓储把 ``None`` 解释成「不改」，所以凭证只能换不能清——云端引擎本来就
        # 不该允许清空（清了必然识别失败）；本地引擎抽不到密文说明数据本来就坏。
        cipher, hint = _seal_secret(kind, fields["secret"])
        changes["secret_cipher"] = cipher
        changes["key_hint"] = hint

    if changes:
        try:
            updated = await repo.update(oid, **changes)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "DuplicateOcrEngine":
                raise _duplicate(str(changes.get("name") or "")) from None
            raise
        if not updated:
            raise _not_found()

    fresh = await repo.get_by_oid(oid) or row
    return public_view(fresh)


async def delete_engine(oid: str) -> None:
    repo = backends.get_ai_ocr_engines_repo()
    if not await repo.delete(oid):
        raise _not_found()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _clean_kind(value: object) -> str:
    kind = str(value or "").strip()
    if kind not in ocr_engines.ENGINES:
        raise RouteError(
            "AI_OCR_ENGINE_BAD_KIND",
            "kind must be one of: " + ", ".join(ocr_engines.ENGINE_KINDS),
            400,
        )
    return kind


def _clean_name(value: object) -> str:
    name = str(value or "").strip()
    if not _NAME_PATTERN.match(name):
        raise RouteError(
            "AI_OCR_ENGINE_INVALID",
            "name must match ^[a-z0-9][a-z0-9_-]{1,39}$",
            400,
        )
    return name


def _clean_config(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RouteError("AI_OCR_ENGINE_INVALID", "config must be an object", 400)
    return dict(value)


def _clean_priority(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RouteError(
            "AI_OCR_ENGINE_INVALID", "priority must be an integer", 400
        )
    if not _PRIORITY_MIN <= value <= _PRIORITY_MAX:
        raise RouteError(
            "AI_OCR_ENGINE_INVALID",
            f"priority must be {_PRIORITY_MIN}..{_PRIORITY_MAX}",
            400,
        )
    return value


def _secret_dict(kind: str, secret: object) -> dict:
    """校验并归一化凭证；``rapidocr`` 只接受空。"""
    required = _REQUIRED_SECRET_FIELDS[kind]
    if secret is None:
        data: dict = {}
    elif isinstance(secret, dict):
        data = dict(secret)
    else:
        raise RouteError("AI_OCR_ENGINE_INVALID", "secret must be an object", 400)

    if not required:
        if data:
            raise RouteError(
                "AI_OCR_ENGINE_INVALID",
                f"{kind} engine takes no credentials",
                400,
            )
        return {}

    for field in required:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RouteError(
                "AI_OCR_ENGINE_INVALID",
                f"{kind} engine requires secret field {field!r}",
                400,
            )
        data[field] = value.strip()

    for key, value in data.items():
        # 凭证值只做形状校验：消息里**不带值**，否则校验失败就等于把 key 写进日志。
        if not isinstance(key, str) or not isinstance(value, str):
            raise RouteError(
                "AI_OCR_ENGINE_INVALID", "secret must be a flat string map", 400
            )
        if not value.strip():
            raise RouteError(
                "AI_OCR_ENGINE_INVALID", f"secret field {key!r} must not be empty", 400
            )
    return data


def _seal_secret(kind: str, secret: object) -> tuple[str | None, str | None]:
    """凭证 dict → ``(secret_cipher, key_hint)``；本地引擎返回 ``(None, None)``。"""
    data = _secret_dict(kind, secret)
    if not data:
        return None, None
    plaintext = json.dumps(data, ensure_ascii=False, sort_keys=True)
    hint_field = _HINT_FIELD.get(kind)
    hint = crypto.key_hint(data.get(hint_field) or "") if hint_field else None
    return crypto.encrypt_secret(plaintext), hint


def _not_found() -> RouteError:
    return RouteError("AI_OCR_ENGINE_NOT_FOUND", "ocr engine not found", 404)


def _duplicate(name: str) -> RouteError:
    return RouteError(
        "AI_OCR_ENGINE_DUPLICATE", f"ocr engine already exists: name={name!r}", 409
    )


__all__ = [
    "create_engine",
    "delete_engine",
    "list_engines",
    "public_view",
    "update_engine",
]
