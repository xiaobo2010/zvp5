"""AI provider credentials, OCR engines, user API keys and the model catalog
(``AI_CREDENTIALS`` / ``AI_OCR_ENGINES`` / ``AI_API_KEYS`` / ``AI_MODELS``).

One module because they share the same secret-handling rules (ciphertext
only, ``key_hint`` for display) and the same duplicate-name exceptions."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from datetime import datetime

from .common import Row, UNSET


# ---------------------------------------------------------------------------
# AI credentials (V025)
# ---------------------------------------------------------------------------


class AICredentialsRepo(Protocol):
    """Data access for ``AI_CREDENTIALS`` (AI provider 凭据, V025).

    Rows use the Mongo document shape::

        {"_id": <hex str>, "scope": "personal" | "shared", "owner_oid": str,
         "name": str, "kind": "openai_compat", "base_url": str,
         "models": list[str], "api_key_cipher": str, "key_hint": str | None,
         "enabled": bool, "created_at": datetime, "updated_at": datetime,
         "expires_at": datetime | None}

    ``models`` is the parsed form of the ``MODELS_JSON`` CLOB (a list of model id
    strings). ``api_key_cipher`` is the **only** form of the key that ever
    reaches the database: AES-GCM ciphertext, base64-encoded. ``key_hint`` holds
    the last four characters for display and is never used to decrypt.
    ``expires_at`` is ``None`` for "never expires" (the historical behaviour);
    the service layer keeps expired rows out of the effective catalog.

    Two kinds of rows share one table: ``personal`` (an admin's own key) and
    ``shared`` (a key everyone may use). Shared rows always carry
    :data:`SHARED_OWNER_OID` as ``owner_oid`` so that the unique index
    ``(scope, owner_oid, name)`` constrains them too.
    """

    async def list_visible(self, user_oid: str) -> list[Row]:
        """该用户可见的全部凭据：自己的 personal + 全部 shared（含 disabled，过滤由服务层做）。"""
        ...

    async def list_all(self) -> list[Row]:
        """Every credential row (admin view), personal rows first."""
        ...

    async def get(self, oid: str) -> Row | None:
        """Return one credential row, or ``None`` when it does not exist."""
        ...

    async def create(
        self,
        *,
        scope: str,
        owner_oid: str,
        name: str,
        kind: str,
        base_url: str,
        models: list[str],
        api_key_cipher: str,
        key_hint: str | None = None,
        enabled: bool = True,
        headers: dict[str, str] | None = None,
        api_path: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        """Insert a credential and return its new ``_id``.

        Raises :class:`db.repos.oracle.ai_credentials.DuplicateCredential` when
        ``(scope, owner_oid, name)`` already exists — the service layer turns
        that into ``409``. A duplicate is never swallowed: silently keeping the
        old key while the admin thinks theirs was saved is the worst outcome.

        ``expires_at=None`` means "never expires".
        """
        ...

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        models: list[str] | None = None,
        api_key_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool | None = None,
        headers: dict[str, str] | None = None,
        api_path: str | None = None,
        expires_at: datetime | None = UNSET,
    ) -> bool:
        """只更新传入的字段；返回是否命中。

        ``None`` 一律表示「不改」，唯一的例外是 ``expires_at``：它的默认值是
        :data:`UNSET`（不改），显式传 ``None`` 才是「清除到期」。
        """
        ...

    async def delete(self, oid: str) -> bool:
        """Delete one credential; ``True`` when a row was removed."""
        ...


# ---------------------------------------------------------------------------
# AI OCR engines (V026)
# ---------------------------------------------------------------------------


class DuplicateOcrEngine(Exception):
    """``name`` 已存在（``ux_ai_ocr_engines_name`` → Oracle -1）。

    与 :class:`db.repos.oracle.ai_credentials.DuplicateCredential` 同形：带上
    撞了的 ``name``，服务层才能把 409 写成「哪个名字重复了」而不是一句「冲突」。
    放在协议文件里而不是 oracle 实现里，是因为调用方（服务层）不该为了捕获它而
    依赖具体后端。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"ocr engine already exists: name={name!r}")


class AIOcrEnginesRepo(Protocol):
    """Data access for ``AI_OCR_ENGINES`` (OCR 引擎注册表, V026).

    Rows use the Mongo document shape::

        {"_id": <hex str>, "name": str,
         "kind": "tencent" | "aliyun" | "baidu" | "groq" | "zhipu",
         "config": dict, "secret_cipher": str | None, "key_hint": str | None,
         "enabled": bool, "priority": int,
         "created_at": datetime, "updated_at": datetime}

    ``config`` is the parsed ``CONFIG_JSON`` CLOB (request-shape knobs such as
    ``language_type``; never credentials). ``secret_cipher`` is the **only**
    form of the credentials that reaches the database: AES-GCM ciphertext of the
    whole credential object, base64-encoded (see :mod:`services.ai.crypto`).
    ``None`` means "this row has no credentials" (legacy rows) — not "missing
    config". ``key_hint`` is display-only.

    ``priority`` is a small-is-first ordering key: the router picks the first
    enabled row, so ``(enabled, priority)`` is both the index and the query
    shape.
    """

    async def list_all(self, *, enabled_only: bool = False) -> list[Row]:
        """Every engine row, ``priority`` ascending then ``name``.

        ``enabled_only`` is the routing path (skip disabled rows in SQL rather
        than in Python); the admin path takes everything so a disabled engine
        stays visible.
        """
        ...

    async def get_by_oid(self, oid: str) -> Row | None:
        """Return one engine row, or ``None`` when it does not exist."""
        ...

    async def insert(
        self,
        *,
        name: str,
        kind: str,
        config: dict | None = None,
        secret_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool = True,
        priority: int = 100,
    ) -> str:
        """Insert an engine and return its new ``_id``.

        Raises :class:`DuplicateOcrEngine` when ``name`` already exists — the
        service layer turns that into ``409``. A duplicate is never swallowed:
        silently keeping the old row while the admin thinks theirs was saved is
        the worst outcome.
        """
        ...

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        config: dict | None = None,
        secret_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> bool:
        """只更新传入的字段（``None`` = 不改）；返回是否命中。"""
        ...

    async def delete(self, oid: str) -> bool:
        """Delete one engine; ``True`` when a row was removed."""
        ...


# ---------------------------------------------------------------------------
# AI API keys (V030)
# ---------------------------------------------------------------------------


class DuplicateApiKey(Exception):
    """``(user_oid, name)`` 或 ``token_hash`` 已存在（唯一索引 → Oracle -1）。

    与 :class:`DuplicateOcrEngine` / ``DuplicateCredential`` 同形：带上撞了的
    ``name``，服务层才能把 409 写成「哪个名字重复了」。名字撞与哈希撞用同一个
    异常：两者对调用方都是「这条记录已经有了」，区分它们只会泄露哈希的存在性。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"api key already exists: name={name!r}")


class AIApiKeysRepo(Protocol):
    """Data access for ``AI_API_KEYS`` (OpenAI 兼容面的独立 API key, V030).

    Rows use the Mongo document shape::

        {"_id": <hex str>, "user_oid": str, "name": str, "token_hash": str,
         "key_hint": str, "enabled": bool, "expires_at": datetime | None,
         "last_used_at": datetime | None, "created_at": datetime}

    ``token_hash`` is ``sha256(token).hexdigest()`` — the plaintext never reaches
    the database or any read path, so a leaked dump yields no usable key.
    ``key_hint`` is display-only (前 8 + 后 4，见 ``services.ai.api_keys``).
    ``expires_at = None`` means "never expires"; ``last_used_at`` is written
    on a sampling basis (at most once per minute), not an audit trail.
    """

    async def list_for(self, user_oid: str) -> list[Row]:
        """Every key of one user, newest first."""
        ...

    async def count_for(self, user_oid: str) -> int:
        """How many keys one user has (the per-user cap is enforced by the caller)."""
        ...

    async def get_by_oid(self, oid: str) -> Row | None:
        """Return one key row, or ``None`` when it does not exist."""
        ...

    async def get_by_hash(self, token_hash: str) -> Row | None:
        """Return the row whose ``token_hash`` matches, or ``None``.

        This is the authentication lookup: one equality predicate on the unique
        index, never a scan of every key.
        """
        ...

    async def insert(
        self,
        *,
        user_oid: str,
        name: str,
        token_hash: str,
        key_hint: str,
        enabled: bool = True,
        expires_at: datetime | None = None,
    ) -> str:
        """Insert a key and return its new ``_id``.

        Raises :class:`DuplicateApiKey` when ``(user_oid, name)`` or
        ``token_hash`` already exists.
        """
        ...

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        expires_at: Any = UNSET,
    ) -> bool:
        """只更新传入的字段（``None`` = 不改），返回是否命中。

        ``expires_at`` 用 :data:`UNSET` 区分「不改」与「显式清空（永不过期）」。
        """
        ...

    async def delete(self, oid: str) -> bool:
        """Delete one key; ``True`` when a row was removed."""
        ...

    async def touch(self, oid: str) -> None:
        """Stamp ``last_used_at`` (sampled: at most once per minute per key)."""
        ...


class AIModelsRepo(Protocol):
    """Data access for ``AI_MODELS``（模型目录覆盖层, V050）。

    Rows use the plain document shape (model 是主键，没有 ``_id``）::

        {"model": str, "display_name": str | None, "provider": str | None,
         "context_length": int | None, "rate": float, "enabled": bool,
         "notes": str | None, "updated_at": datetime,
         "updated_by": str | None}

    这张表**只存覆盖**：缺少某模型的行意味着「用默认口径（rate=1、
    enabled=True）」，不是「这个模型不存在」——模型是否存在由
    ``AI_CREDENTIALS.MODELS_JSON`` 决定。

    :meth:`upsert` 的 ``fields`` 只携带**调用方明确给了的**字段：没出现的键保持
    原值（新行则落默认值）。所以「不改倍率、只改展示名」不会把倍率重置成 1。
    """

    async def list_all(self) -> list[Row]:
        """全部覆盖行，按模型名升序。"""
        ...

    async def get(self, model: str) -> Row | None:
        """取单个模型的覆盖行；没有配置过返回 ``None``。"""
        ...

    async def upsert(
        self,
        model: str,
        *,
        fields: Mapping[str, Any],
        actor: str | None = None,
    ) -> Row:
        """按模型名插入或部分更新，返回落库后的行。

        ``fields`` 可含 ``display_name`` / ``provider`` / ``context_length`` /
        ``rate`` / ``enabled`` / ``notes``；``updated_at`` 与 ``updated_by`` 由
        本层维护。校验（rate 区间、长度、正整数）属于路由层，这一层不重复判断。
        """
        ...

    async def delete(self, model: str) -> bool:
        """删除一个覆盖行（不动凭据里的模型）；``True`` 表示真的删掉了。"""
        ...
