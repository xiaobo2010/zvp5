"""会话内对话的 SSE 端点（``POST /api/ai/conversations/{oid}/chat``，契约 §3.3）。

这一层最容易出事的四件事，全部钉在这里：

* **门禁顺序**：mode 不匹配 / 非本人必须在返回 ``StreamingResponse`` 之前变成
  可解析的 JSON——响应头一旦发出，409 就只能藏在流里，前端的分支会全错。
* **落库边界**：用户消息先写、assistant 只有上游正常结束才写；中断时库里不能出现
  「模型说过但没人见过」的半句话。
* **上下文顺序**：``system`` → 摘要 ``system`` → 历史 → 本轮（§5.1）。摘要跑到
  尾巴上会让前缀指纹退化成全站同 key，而单测是唯一能便宜地钉住顺序的地方。
* **usage 的 NULL 语义**：上游没报 usage 时 token 列留 NULL、units 走估算，但会话
  的水位仍要前进（否则压缩永远不触发）。

上游 / 凭据 / 配额 / 仓储全部内存桩：不连库、不出网、不花真实额度。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from db import backends
from routers import ai_chat_conversations_router as chat_router
from services.ai import credentials, providers, quota, rate_limit, routing
from util.object_id import get_current_user

OWNER = "0" * 24
OTHER = "1" * 24
CONVERSATION = "c" * 32
SECRET = "sk-unit-test-0123456789abcdef"
ENTRY_OID = "b" * 24


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class Recorder:
    """记录调用次数的异步桩。"""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class FakeConversationsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def seed(self, oid: str = CONVERSATION, *, owner_oid: str = OWNER, mode: str = "chat",
             title: str | None = None, summary: str | None = None,
             summary_upto_seq: int = 0) -> dict:
        row = {
            "_id": oid,
            "owner_oid": owner_oid,
            "mode": mode,
            "title": title,
            "model": "gpt-x",
            "status": "active",
            "summary": summary,
            "summary_upto_seq": summary_upto_seq,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "context_tokens": 0,
            "message_count": 0,
            "compaction_degraded_at": None,
        }
        self.rows[oid] = row
        return row

    async def get(self, oid: str, owner_oid: str) -> dict | None:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return None
        return dict(row)

    async def update(self, oid, owner_oid, *, title=None, status=None, model=None,
                     clear_model=False) -> bool:
        row = self.rows.get(str(oid))
        if row is None or str(row["owner_oid"]) != str(owner_oid):
            return False
        if title is not None:
            row["title"] = title
        return True


class FakeMessagesRepo:
    """按真实仓储的语义分配 ``seq`` 并回写会话的冗余计数/用量。"""

    def __init__(self, conv: FakeConversationsRepo) -> None:
        self.conv = conv
        self.rows: list[dict] = []
        self.batches: list[dict] = []

    def seed(self, *, seq: int, role: str, content: str, conversation_oid: str = CONVERSATION,
             **extra) -> dict:
        row = {
            "_id": f"m{seq:031d}",
            "conversation_oid": conversation_oid,
            "seq": seq,
            "role": role,
            "content": content,
            "tool_calls": None,
            "tool_call_id": None,
            "tool_name": None,
            "round_no": None,
            "finish_reason": None,
            "request_id": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
        row.update(extra)
        self.rows.append(row)
        return row

    async def append_batch(self, conversation_oid, rows, *, usage=None):
        self.batches.append(
            {
                "conversation_oid": conversation_oid,
                "rows": [dict(row) for row in rows],
                "usage": usage,
            }
        )
        base = max(
            [
                row["seq"]
                for row in self.rows
                if row["conversation_oid"] == conversation_oid
            ],
            default=0,
        )
        inserted: list[dict] = []
        for offset, item in enumerate(rows):
            row = dict(item)
            row["seq"] = base + offset + 1
            row["conversation_oid"] = conversation_oid
            self.rows.append(row)
            inserted.append(row)

        record = self.conv.rows.get(str(conversation_oid))
        if record is not None:
            record["message_count"] += len(inserted)
            if usage is not None:
                record["tokens_prompt"] += int(usage.get("prompt_tokens") or 0)
                record["tokens_completion"] += int(usage.get("completion_tokens") or 0)
                record["tokens_total"] += int(usage.get("total_tokens") or 0)
                if usage.get("context_tokens") is not None:
                    record["context_tokens"] = int(usage["context_tokens"])
        return inserted

    async def list_after(self, conversation_oid, after_seq=0, *, limit=50):
        rows = [
            dict(row)
            for row in self.rows
            if row["conversation_oid"] == conversation_oid and row["seq"] > after_seq
        ]
        rows.sort(key=lambda row: row["seq"])
        return rows[:limit]


def _entry(name: str = "team") -> dict:
    return {
        "key": f"shared:{ENTRY_OID}",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": name,
        "kind": "openai_compat",
        "base_url": f"https://{name}.example.com/v1",
        "models": ["gpt-x"],
        "api_key_cipher": "cipher-" + name,
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": ENTRY_OID,
        "created_at": None,
        "updated_at": None,
    }


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api/ai")
    identity = lambda: user or {"id": OWNER, "per": ["admin"]}  # noqa: E731
    app.dependency_overrides[get_current_user] = identity
    return TestClient(app)


def _frames(body: str) -> list[str]:
    frames: list[str] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        assert block.startswith("data: "), block
        frames.append(block[len("data: ") :])
    return frames


DELTA = 'data: {{"choices":[{{"delta":{{"content":"{}"}},"finish_reason":null}}]}}\n\n'
USAGE = (
    'data: {{"choices":[{{"delta":{{}},"finish_reason":"stop"}}],'
    '"usage":{{"prompt_tokens":{}, "completion_tokens":{}, "total_tokens":{}}}}}\n\n'
)
DONE = "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def _isolated_gateway(monkeypatch, quiet_ai_governance):
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    # 闸门是进程级 Semaphore，绑在创建它的 event loop 上；TestClient 每个请求一个
    # 新 loop，跨用例复用会炸。
    routing._semaphore = None
    routing._semaphore_size = 0
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.fixture
def env(monkeypatch) -> dict:
    conv = FakeConversationsRepo()
    msgs = FakeMessagesRepo(conv)
    audit = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_conversations_repo", lambda session=None: conv)
    monkeypatch.setattr(backends, "get_ai_messages_repo", lambda session=None: msgs)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)

    async def _catalog(user_oid: str) -> list[dict]:
        return [_entry()]

    async def _resolve(entry: dict) -> str:
        return SECRET

    peek = Recorder((True, 0, 100))
    consume = Recorder((True, 1, 100))
    monkeypatch.setattr(credentials, "catalog", _catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)

    return {
        "conv": conv,
        "msgs": msgs,
        "audit": audit,
        "peek": peek,
        "consume": consume,
    }


@pytest.fixture
def upstream(monkeypatch):
    """上游流桩：按顺序 yield bytes，遇到异常对象直接抛；``captured`` 留请求体。"""

    state: dict = {"calls": [], "payloads": []}

    def install(*items):
        async def _stream(payload: dict, entry: dict, api_key: str):
            state["calls"].append(entry)
            state["payloads"].append(payload)
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        monkeypatch.setattr(providers, "chat_completion_stream", _stream)
        return state

    return install


def _chat(client: TestClient | None = None, **payload):
    body = {"content": "你好"}
    body.update(payload)
    return (client or _client()).post(
        f"/api/ai/conversations/{CONVERSATION}/chat", json=body
    )


# ---------------------------------------------------------------------------
# 门禁
# ---------------------------------------------------------------------------

def test_mode_mismatch_is_409_before_any_upstream_call(env, upstream):
    env["conv"].seed(mode="agent")
    state = upstream()

    response = _chat()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AI_CONVERSATION_MODE_MISMATCH"
    # 一次上游都没打、一条消息都没写：拒绝必须发生在产生任何副作用之前。
    assert state["calls"] == []
    assert env["msgs"].rows == []


def test_foreign_conversation_is_404_not_403(env, upstream):
    env["conv"].seed(owner_oid=OWNER)
    state = upstream()

    response = _chat(_client({"id": OTHER, "per": ["admin"]}))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AI_CONVERSATION_NOT_FOUND"
    assert state["calls"] == []
    assert env["msgs"].rows == []


def test_without_ai_access_is_403_with_an_audit_row(env, upstream):
    env["conv"].seed()
    state = upstream()

    response = _chat(_client({"id": OWNER, "per": ["student"]}))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
    assert state["calls"] == []
    assert [row["status"] for row in env["audit"].rows] == ["rejected"]


def test_payload_validation_rejects_blank_content(env, upstream):
    env["conv"].seed()
    state = upstream()

    response = _chat(content="   ")

    assert response.status_code == 400
    assert state["calls"] == []


# ---------------------------------------------------------------------------
# 正常流
# ---------------------------------------------------------------------------

def test_successful_stream_persists_both_messages_with_contiguous_seq(env, upstream):
    env["conv"].seed(title=None)
    upstream(
        DELTA.format("你").encode(),
        DELTA.format("好呀").encode(),
        USAGE.format(120, 30, 150).encode(),
        DONE.encode(),
    )

    response = _chat()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    head = json.loads(frames[0])
    assert head["conversation_oid"] == CONVERSATION
    # 首帧的 message_oid 就是那条已落库的用户消息（客户端拿它对乐观气泡）。
    user_rows = [row for row in env["msgs"].rows if row["role"] == "user"]
    assert head["message_oid"] == user_rows[0]["oid"]
    assert frames[-1] == "[DONE]"

    rows = env["msgs"].rows
    assert [(row["role"], row["seq"]) for row in rows] == [("user", 1), ("assistant", 2)]
    assert rows[0]["content"] == "你好"
    assert rows[1]["content"] == "你好呀"
    # 两条消息的 request_id 与审计行同源：一条对话能被串起来。
    assert rows[0]["request_id"] == rows[1]["request_id"] == env["audit"].rows[0]["request_id"]
    assert rows[1]["round_no"] == 1
    assert rows[1]["finish_reason"] == "stop"
    assert rows[1]["prompt_tokens"] == 120
    assert rows[1]["completion_tokens"] == 30
    assert rows[1]["total_tokens"] == 150

    record = env["conv"].rows[CONVERSATION]
    assert record["message_count"] == 2
    assert record["tokens_total"] == 150
    assert record["context_tokens"] == 120
    # 首条用户消息补标题（§2.1）。
    assert record["title"] == "你好"

    audit_row = env["audit"].rows[0]
    assert audit_row["status"] == "success"
    assert audit_row["endpoint"] == chat_router.ENDPOINT_CHAT_CONVERSATION
    assert audit_row["purpose"] == "chat"
    assert audit_row["round_no"] == 1
    assert audit_row["total_tokens"] == 150
    assert audit_row["cache_key"] is None  # 该 host 不在缓存白名单里
    assert env["consume"].calls[0][0] == (OWNER, "chat", 1)


def test_upstream_payload_is_stateless_messages_only(env, upstream):
    env["conv"].seed()
    env["msgs"].seed(seq=1, role="user", content="旧问题")
    env["msgs"].seed(seq=2, role="assistant", content="旧回答")
    state = upstream(DELTA.format("好").encode(), DONE.encode())

    response = _chat()

    assert response.status_code == 200
    payload = state["payloads"][0]
    assert payload["stream"] is True
    assert payload["model"] == "gpt-x"
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    # 历史只带正文与角色：内部字段（oid / usage / created_at）不能流到上游。
    for message in payload["messages"]:
        assert set(message) <= {"role", "content", "tool_calls"}
    assert payload["messages"][-1]["content"] == "你好"


def test_upstream_failure_midstream_leaves_no_assistant_and_records_error(env, upstream):
    env["conv"].seed()
    upstream(
        DELTA.format("半").encode(),
        routing.RouteError("AI_UPSTREAM_REJECTED", "upstream rejected", 502),
    )

    response = _chat()

    assert response.status_code == 200  # 头早就发出去了
    frames = _frames(response.text)
    assert json.loads(frames[-2])["error"]["code"] == "AI_UPSTREAM_REJECTED"
    assert frames[-1] == "[DONE]"

    # assistant 不落库：库里只有那条用户消息。
    assert [row["role"] for row in env["msgs"].rows] == ["user"]
    assert env["conv"].rows[CONVERSATION]["message_count"] == 1
    assert env["conv"].rows[CONVERSATION]["tokens_total"] == 0

    audit_row = env["audit"].rows[0]
    assert audit_row["status"] == "rejected"
    assert audit_row["error_code"] == "AI_UPSTREAM_REJECTED"
    assert audit_row["total_tokens"] is None
    # 上游已经产生了成本：估算扣减仍然发生（与 /v1 的流式口径一致）；没有 usage
    # 时单位数是按字符估出来的，只要求「至少 1」。
    assert env["consume"].calls[0][0][:2] == (OWNER, "chat")
    assert env["consume"].calls[0][0][2] >= 1


def test_missing_usage_falls_back_to_estimation_but_moves_the_watermark(env, upstream):
    env["conv"].seed()
    upstream(DELTA.format("没有 usage").encode(), DONE.encode())

    response = _chat()

    assert response.status_code == 200
    assistant = [row for row in env["msgs"].rows if row["role"] == "assistant"][0]
    # NULL = 上游没报（不是 0）：视图据此显示「—」。
    assert assistant["prompt_tokens"] is None
    assert assistant["completion_tokens"] is None
    assert assistant["total_tokens"] is None

    usage_block = env["msgs"].batches[-1]["usage"]
    assert usage_block["context_tokens"] > 0
    assert env["conv"].rows[CONVERSATION]["context_tokens"] == usage_block["context_tokens"]
    assert env["conv"].rows[CONVERSATION]["tokens_total"] == 0

    audit_row = env["audit"].rows[0]
    assert audit_row["total_tokens"] is None
    # 没有实测：units 只能是估算（至少 1），token 列全 NULL。
    assert audit_row["units"] >= 1


def test_compaction_summary_is_the_second_system_message(env, upstream):
    env["conv"].seed(summary="早先聊过甲，结论是乙", summary_upto_seq=2)
    env["msgs"].seed(seq=1, role="user", content="已被压缩的问题")
    env["msgs"].seed(seq=2, role="assistant", content="已被压缩的回答")
    env["msgs"].seed(seq=3, role="user", content="还在上下文里的问题")
    env["msgs"].seed(seq=4, role="assistant", content="还在上下文里的回答")
    state = upstream(DELTA.format("好").encode(), DONE.encode())

    response = _chat()

    assert response.status_code == 200
    messages = state["payloads"][0]["messages"]
    # 稳定前缀：版本化提示在前，摘要在第二条 system（放尾部会让前缀指纹退化）。
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("[ops-v1]")
    assert messages[1]["role"] == "system"
    assert "早先聊过甲，结论是乙" in messages[1]["content"]
    # 摘要之前（seq <= summary_upto_seq）的原文不进上下文。
    contents = [message.get("content") for message in messages]
    assert "已被压缩的问题" not in contents
    assert "已被压缩的回答" not in contents
    assert contents[-3:-1] == ["还在上下文里的问题", "还在上下文里的回答"]
    # 本轮消息在最后。
    assert messages[-1] == {"role": "user", "content": "你好"}


def test_quota_exhausted_is_429_without_writing_anything(env, upstream, monkeypatch):
    env["conv"].seed()
    state = upstream()
    monkeypatch.setattr(quota, "peek", Recorder((False, 100, 100)))

    response = _chat()

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "insufficient_quota"
    assert response.headers["X-AI-Quota-Used"] == "100"
    assert state["calls"] == []
    assert env["msgs"].rows == []


def test_empty_upstream_stream_still_persists_an_empty_answer(env, upstream):
    """上游正常结束但一个字都没给：这是一次「空回答」，不是中断。"""
    env["conv"].seed()
    upstream(DONE.encode())

    response = _chat()

    assert response.status_code == 200
    rows = env["msgs"].rows
    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "你好"),
        ("assistant", ""),
    ]


class _Guard:
    """``routing.slot()`` 的替身：只关心「有且只有一次释放」。"""

    def __init__(self) -> None:
        self.exits = 0

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc_info) -> bool:
        self.exits += 1
        return False


@pytest.mark.asyncio
async def test_client_disconnect_keeps_the_assistant_out_of_the_db(env, upstream):
    """客户端中途断开：assistant 不落库，审计记 error，闸门必须释放。

    这条路径走不到 HTTP 层（TestClient 会把整个流缓冲完），所以直接驱动生成器：
    断开在真实部署里就是「协程被取消」，等价于这里 ``__anext__`` 抛
    ``CancelledError``。
    """
    env["conv"].seed()
    upstream(
        DELTA.format("半").encode(),
        asyncio.CancelledError(),
    )
    guard = _Guard()
    turn = {
        "conversation_oid": CONVERSATION,
        "owner_oid": OWNER,
        "message_oid": "u" * 32,
        "request_id": "r" * 32,
        "model": "gpt-x",
        "entry": _entry(),
        "api_key": SECRET,
        "purpose": "chat",
        "roles": ["admin"],
        "roles_snapshot": "admin",
        "body": {"model": "gpt-x", "messages": [], "stream": True},
        "cache_key": None,
        "prompt_chars": 10,
        "prompt_tokens_estimate": 3,
    }

    stream = chat_router._stream_turn(turn, guard)
    await stream.__anext__()  # 首帧
    await stream.__anext__()  # 上游的第一段字节
    with pytest.raises(__import__("asyncio").CancelledError):
        await stream.__anext__()

    assert guard.exits == 1
    assert env["msgs"].rows == []
    assert env["audit"].rows[0]["status"] == "error"
    assert env["audit"].rows[0]["error_code"] == chat_router.ABORTED_CODE
