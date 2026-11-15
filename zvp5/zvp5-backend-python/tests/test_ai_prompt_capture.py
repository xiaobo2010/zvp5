"""``services.ai.prompt_audit``：受控留存的捕获条件、脱敏、截断与旁路语义。

这张表是「AI_CALL_LOGS 零正文」的唯一例外，所以每条都单独钉：

* **谁留**：默认 ``department``（学生会）与 ``admin``；admin 自查同样需要留痕。
  捕获角色可配置，空集合 = 关闭；判定只看 roles 交集。
* **留什么**：最后一条 ``role=user`` 的正文；content 数组取文本段。
* **怎么留**：18 位身份证 / 11 位手机号先脱敏、再按 ``AI_PROMPT_MAX_CHARS`` 截断
  （顺序反了会切出半个号码，正则就再也遮不住）。
* **失败怎么办**：写入抛任何异常都只记一条不含正文的 warning，主请求照常。

全部离线：仓储是内存桩，不连库、不出网。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import config
from db import backends
from services.ai import config as ai_config
from services.ai import prompt_audit

USER = "000000000000000000000003"
SECRETARY = {"id": USER, "per": ["department"]}
ADMIN = {"id": USER, "per": ["admin"]}


class FakePromptAuditRepo:
    """只实现 ``insert``：捕获路径只会用到它。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.rows: list[dict] = []
        self.error = error

    async def insert(self, **fields) -> str:
        if self.error is not None:
            raise self.error
        self.rows.append(dict(fields))
        return "a" * 24


@pytest.fixture()
def repo(monkeypatch) -> FakePromptAuditRepo:
    fake = FakePromptAuditRepo()
    monkeypatch.setattr(
        backends, "get_ai_prompt_audit_repo", lambda session=None: fake
    )
    return fake


def _messages(*items: dict) -> list[dict]:
    return list(items)


def _capture(user=SECRETARY, messages=None, purpose="chat", request_id="req-1"):
    return asyncio.run(
        prompt_audit.capture(
            user=user,
            purpose=purpose,
            messages=messages
            if messages is not None
            else _messages({"role": "user", "content": "你好"}),
            request_id=request_id,
        )
    )


# ---------------------------------------------------------------------------
# 捕获条件：谁留 / 谁不留
# ---------------------------------------------------------------------------


def test_department_prompt_is_captured(repo):
    assert _capture() == "a" * 24
    (row,) = repo.rows
    assert row["user_oid"] == USER
    assert row["purpose"] == "chat"
    assert row["request_id"] == "req-1"
    assert row["prompt_text"] == "你好"
    assert row["roles_snapshot"] == "department"


def test_admin_prompt_is_captured_by_default(repo):
    """默认集合含 admin：管理员自查也要留痕（否则「谁问过什么」会断在管理员这一环）。"""
    assert _capture(user=ADMIN) == "a" * 24
    (row,) = repo.rows
    assert row["roles_snapshot"] == "admin"


def test_capture_is_a_roles_intersection(repo):
    """多角色账号命中任一捕获角色即留（只看交集，不按优先级取一个）。"""
    both = {"id": USER, "per": ["admin", "department"]}

    assert _capture(user=both) == "a" * 24
    assert repo.rows[0]["roles_snapshot"] == "admin,department"
    assert _capture(user={"id": USER, "per": ["student", "admin"]}) == "a" * 24


def test_capture_roles_are_configurable(repo, monkeypatch):
    monkeypatch.setattr(
        config, "AI_PROMPT_CAPTURE_ROLES", ("secretary",), raising=False
    )

    assert _capture(user=SECRETARY) is None
    assert _capture(user={"id": USER, "per": ["secretary"]}) == "a" * 24
    assert len(repo.rows) == 1


def test_empty_capture_roles_turns_the_capture_off(repo, monkeypatch):
    """空集合是显式关闭（与 ``AI_ALLOWED_ROLES`` 的「空 = 写坏」语义不同）。"""
    monkeypatch.setattr(config, "AI_PROMPT_CAPTURE_ROLES", (), raising=False)

    assert _capture() is None
    assert repo.rows == []


def test_caller_without_roles_is_never_captured(repo):
    assert _capture(user={"id": USER}) is None
    assert _capture(user={"id": USER, "per": None}) is None
    assert repo.rows == []


# ---------------------------------------------------------------------------
# 留什么：最后一条 user 消息
# ---------------------------------------------------------------------------


def test_last_user_message_wins(repo):
    _capture(
        messages=_messages(
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        )
    )

    assert repo.rows[0]["prompt_text"] == "第二问"


def test_no_user_message_means_nothing_to_capture(repo):
    assert _capture(messages=_messages({"role": "system", "content": "x"})) is None
    assert _capture(messages=[]) is None
    assert _capture(messages="not-a-list") is None
    assert repo.rows == []


def test_content_array_keeps_text_parts_only(repo):
    _capture(
        messages=_messages(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图讲了什么"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        )
    )

    assert repo.rows[0]["prompt_text"] == "这张图讲了什么"


# ---------------------------------------------------------------------------
# 脱敏与截断
# ---------------------------------------------------------------------------


def test_id_card_is_masked_keeping_the_first_three_and_last_four():
    masked = prompt_audit.mask_pii("学生身份证 110101199003071234 请记住")

    assert masked == "学生身份证 110***********1234 请记住"


def test_id_card_with_x_checksum_is_masked_too():
    assert prompt_audit.mask_pii("11010119900307123X") == "110***********123X"


def test_mobile_number_is_masked_keeping_the_first_three_and_last_two():
    assert prompt_audit.mask_pii("电话 13812345678 找我") == "电话 138******78 找我"


def test_other_numbers_are_left_alone():
    """学号、班级号、token 数都不是身份证/手机号：多遮只会把正常内容改坏。"""
    text = "学号 20230101，班级 12，token 1234567"
    assert prompt_audit.mask_pii(text) == text


def test_capture_masks_before_it_truncates(repo, monkeypatch):
    """先截断会切出半个身份证号，正则就再也遮不住——顺序是承重的。"""
    monkeypatch.setattr(config, "AI_PROMPT_MAX_CHARS", 12, raising=False)

    _capture(messages=_messages({"role": "user", "content": "110101199003071234"}))

    # 12 字符上限内，掩码已经生效（前 3 位 + 星号），不会出现完整的中间 8 位。
    stored = repo.rows[0]["prompt_text"]
    assert stored == "110*********"
    assert "19900307" not in stored


def test_long_prompt_is_truncated_to_the_configured_limit(repo, monkeypatch):
    monkeypatch.setattr(config, "AI_PROMPT_MAX_CHARS", 100, raising=False)

    _capture(messages=_messages({"role": "user", "content": "啊" * 500}))

    assert repo.rows[0]["prompt_text"] == "啊" * 100


def test_default_limit_is_eight_thousand_chars(repo):
    _capture(messages=_messages({"role": "user", "content": "a" * 9000}))

    assert len(repo.rows[0]["prompt_text"]) == ai_config.prompt_max_chars() == 8000


# ---------------------------------------------------------------------------
# 旁路：写入失败绝不冒泡、也绝不把正文写进日志
# ---------------------------------------------------------------------------


def test_insert_failure_does_not_raise_and_hides_the_body(caplog):
    boom = FakePromptAuditRepo(error=RuntimeError("ORA-00942 table not found"))
    backends.get_ai_prompt_audit_repo = lambda session=None: boom  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        assert _capture(
            messages=_messages({"role": "user", "content": "机密正文-不要进日志"})
        ) is None

    assert "ai prompt audit capture failed" in caplog.text
    assert "RuntimeError" in caplog.text
    # warning 里只有异常类名：正文与 SQL/DSN 都不许出现。
    assert "机密正文" not in caplog.text
    assert "ORA-00942" not in caplog.text


def test_missing_user_id_is_skipped(repo):
    assert _capture(user={"id": "", "per": ["department"]}) is None
    assert repo.rows == []


def test_blank_prompt_is_skipped(repo):
    assert _capture(messages=_messages({"role": "user", "content": "   "})) is None
    assert repo.rows == []


# ---------------------------------------------------------------------------
# agent：一次 run 只留一次
# ---------------------------------------------------------------------------


def test_agent_run_captures_the_prompt_once_per_run(repo, monkeypatch):
    """executor 的挂点在 run 入口，不在每轮循环里——所以一次 run 只有一行。"""
    from services.agent import executor

    async def _chat_completion(payload, entry, api_key):
        return {"choices": [{"message": {"content": "答完"}}], "usage": {}}

    async def _catalog(user_oid):
        return [
            {
                "key": "shared:x",
                "scope": "shared",
                "name": "lab",
                "kind": "openai_compat",
                "base_url": "https://lab.example.com/v1",
                "models": ["gpt-x"],
                "api_key_cipher": "cipher",
                "enabled": True,
                "oid": "b" * 24,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def _resolve(entry):
        return "sk-unit-test"

    class _Peek:
        async def __call__(self, *args, **kwargs):
            return (True, 0, 100)

    async def _consume(*args, **kwargs):
        return (True, 1, 100)

    from services.ai import providers, quota, rate_limit, routing
    from services.ai import credentials

    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0

    monkeypatch.setattr(credentials, "catalog", _catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(providers, "chat_completion", _chat_completion)
    monkeypatch.setattr(quota, "peek", _Peek())
    monkeypatch.setattr(quota, "consume", _consume)

    import settings

    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(executor.risk_monitor, "evaluate", _noop)
    monkeypatch.setattr(
        backends, "get_ai_call_logs_repo", lambda session=None: FakeAudit()
    )

    result = asyncio.run(
        executor.run(
            user=SECRETARY,
            messages=[{"role": "user", "content": "帮我查一下活动"}],
            model="gpt-x",
        )
    )

    assert result["content"] == "答完"
    (row,) = repo.rows
    assert row["purpose"] == "agent"
    assert row["prompt_text"] == "帮我查一下活动"
    assert row["request_id"] == result["request_id"]


class FakeAudit:
    async def insert(self, **fields):
        return "a" * 24
