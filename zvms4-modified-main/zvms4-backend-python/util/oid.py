"""对象 id 的 HTTP 层收敛点：正则只有一处，权威判定仍在 ``db.ids``。

``db.ids`` 是 id 的**唯一**权威实现（24 位 hex，非法抛 ``InvalidOid``）。HTTP
层还各自需要自己的错误码/文案（兼容契约），这些包装放在这里，只做
``strip``/``lower`` 再调 ``db.ids``，不再各写一遍正则与大小写处理。

注意形状**不止一种**：业务 oid 是 24 位 hex，AI 会话/运行队列的 id 是
``uuid4().hex``（32 位）。长度不同的调用点用 :func:`hex_id_or_none` 传长度，
24 位那条路径始终走 :func:`db.ids.normalize_oid`。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from db.ids import OID_HEX_LENGTH, InvalidOid, normalize_oid

# 旧 ``utils.validate_object_id`` 的契约文案：非法对象 id 一律 400 + 这句。
INVALID_OID_DETAIL = "Invalid Object ID"


def validate_object_id(value: Any) -> str:
    """24 位 hex oid → 小写；非法一律 ``HTTP 400`` + :data:`INVALID_OID_DETAIL`。

    这是 ``utils.validate_object_id``（已删除）与
    ``util.object_id.validate_object_id`` 的唯一实现：两者过去是逐字节相同的
    两份拷贝，异常类型（``HTTPException``，不是 ``InvalidOid``）与文案都必须
    保持不变，否则前端拿到的 400 body 会从英文变成全局处理器的中文。
    """
    try:
        return normalize_oid(value)
    except InvalidOid:
        raise HTTPException(status_code=400, detail=INVALID_OID_DETAIL)


def hex_id_or_none(value: Any, *, length: int = OID_HEX_LENGTH) -> str | None:
    """``strip`` + 小写后按长度校验，非法回 ``None``（由调用方决定错误码）。

    不做异常：调用方各有各的 404/400 语义与错误码，这里只负责「形状」。
    """
    text = str(value or "").strip().lower()
    if length == OID_HEX_LENGTH:
        # 权威实现只有一份：24 位 oid 一律问 db.ids。
        try:
            return normalize_oid(text)
        except InvalidOid:
            return None
    if len(text) != length or not text.isascii():
        return None
    return text if all(char in "0123456789abcdef" for char in text) else None


__all__ = ["INVALID_OID_DETAIL", "hex_id_or_none", "validate_object_id"]
