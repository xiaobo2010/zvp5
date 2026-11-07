"""oid 形状守卫：24 位 hex 是业务 oid，32 位 hex 是会话/提案的 opaque handle。

拆分 ``routers/ai_router.py`` 时曾把会话/提案的 32 位形状报成「写错了，ObjectId
是 24 位」。核对三个权威来源后确认 **32 位才是对的**（会话/run 的形状判定现在
统一走 ``util.oid.hex_id_or_none(length=32)``）：

* ``db/ids.py`` 的 24 位 hex 只用于业务 ObjectId（``new_oid`` / ``validate_oid``）；
* ``db/repos/protocols.py`` 明确记录会话与 run 的 ``oid`` 是
  「32-hex ``uuid4().hex`` used directly as the opaque handle」；
* 写入点 ``services/ai/conversations.py`` 与 ``services/agent/proposal.py``
  都用 ``uuid4().hex``。

把这两个正则改成 ``{24}`` 会让**所有**会话/消息/run/提案端点把正常 id 当成
「不存在」回 404。本用例把这条边界钉住，下次再有人想「顺手修 bug」时会先变红。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import db.ids as ids
from routers.ai_agent_router import PROPOSAL_OID_PATTERN
from routers.ai_conversations_router import _require_oid
from util.oid import hex_id_or_none
from services.agent.tool_impl import AgentError


def test_business_object_id_is_24_hex() -> None:
    """业务 oid 的权威口径：24 位 hex（大小写都收，输出小写）。"""
    raw = "ABCDEF0123456789ABCDEF01"
    assert len(raw) == ids.OID_HEX_LENGTH == 24
    assert ids.is_valid_oid(raw)
    assert ids.validate_oid(raw) == raw.lower()
    assert not ids.is_valid_oid(uuid4().hex)  # 32 位不是业务 oid


def test_conversation_handle_is_32_hex_uuid4() -> None:
    """会话/消息/run 的 oid 是 ``uuid4().hex``：32 位小写 hex。"""
    handle = uuid4().hex
    assert len(handle) == 32
    assert hex_id_or_none(handle, length=32) == handle
    # 24 位业务 oid 不该被当成会话 handle
    assert hex_id_or_none("a" * 24, length=32) is None
    # 形状判定本身只收小写 hex；大写是包装层先 lower 再校验（见下一条断言）
    assert hex_id_or_none(handle.upper(), length=32) == handle


def test_proposal_handle_is_32_hex_uuid4() -> None:
    """提案 oid 同样是 ``uuid4().hex``（``services/agent/proposal.py`` 写入）。"""
    handle = uuid4().hex
    assert PROPOSAL_OID_PATTERN.match(handle)
    assert not PROPOSAL_OID_PATTERN.match("a" * 24)


def test_conversation_rejects_a_business_object_id_as_not_found() -> None:
    """24 位 oid 落到会话路径上 = 「不存在」（404），不是 400/500。"""
    with pytest.raises(AgentError) as excinfo:
        _require_oid("a" * 24, code="NOT_FOUND", message="conversation not found")
    assert excinfo.value.status_code == 404
