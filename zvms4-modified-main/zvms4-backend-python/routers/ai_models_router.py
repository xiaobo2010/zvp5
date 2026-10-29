"""AI 模型目录（``/api/ai/models``，admin）：Agent Fuel 倍率与元数据。

模型名的**事实来源**始终是 ``AI_CREDENTIALS.MODELS_JSON``（哪个模型配在哪个渠道
里）；``AI_MODELS`` 只是稀疏覆盖层（展示名 / 上下文窗口 / 倍率 / 启停）。所以：

* ``GET`` 返回两者的**并集**——只列 ``AI_MODELS`` 会让新配好的渠道模型凭空消失，
  只列渠道又没法管理「已停用 / 待接入」的自定义模型行；
* ``PUT`` 允许给任何模型名建覆盖行（含还没接进渠道的自定义模型）；
* ``DELETE`` 只删覆盖行，凭据里的模型列表不受影响——删配置 = 回到默认倍率，不是
  把模型从目录里删掉。

门禁只有 ``admin``（``_require_admin``），**不走**双人 TOTP：这里改的是展示与计费
口径，不是密钥。唯一例外是 ``POST /discover``——它会解密凭据里的上游 key 去打
真实请求，所以额外挂密码再认证（见该端点 docstring）。

计费换算（rate → Agent Fuel）在另一条车道：本模块只负责把 rate 存对、读对。
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import backends
from routers.ai_router import _require_admin
from services.ai import config as ai_config
from services.ai import credentials, model_rates, providers
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from util.object_id import get_current_user, require_password_verification

router = APIRouter()

# 列宽与 db/models.py / V050 的 DDL 一一对应：路由先挡一次，数据库才不会用
# ORA-12899 把「名字太长」变成 500。
MODEL_MAX = 80
DISPLAY_NAME_MAX = 120
PROVIDER_MAX = 40
NOTES_MAX = 400
#: 倍率的合理上限。1 = 基准价；1000 已经足够表达「贵三个数量级」的模型，
#: 再高只会是填错（多打几个零），挡在路由层比让账单炸掉好。
RATE_MAX = 1000.0
#: ``context_length`` 是 ``NUMBER(10)``：上限取 10 亿 token（远大于任何真实窗口）。
CONTEXT_MAX = 999_999_999

#: 覆盖行允许写的字段（与仓储层的白名单一致）。
_FIELD_KEYS = (
    "display_name",
    "provider",
    "context_length",
    "rate",
    "enabled",
    "notes",
)


class ModelUpdateRequest(BaseModel):
    """``PUT`` 的部分更新体：**没传的字段保持原值**，显式 ``null`` 表示清空。

    ``rate`` / ``context_length`` / ``enabled`` 的取值区间在这里是宽松类型，
    真正的区间校验在 :func:`_clean_fields`：要让非法值回 400（业务错误）而不是
    FastAPI 的 422（Pydantic 结构错误），文案也才能是中文。
    """

    display_name: str | None = None
    provider: str | None = None
    context_length: int | None = Field(default=None, strict=True)
    rate: float | None = Field(default=None, strict=True)
    enabled: bool | None = None
    notes: str | None = None


class DiscoverRequest(BaseModel):
    """「从上游拉取模型元数据」的请求体。

    ``apply=False`` 只预览（不写库）；``apply=True`` 才把上游的上下文窗口合并回
    凭据。``verification_token`` 与凭据写路径同形：这条动作会解密并使用真实上游
    密钥，属于高危操作。
    """

    provider: str
    apply: bool = False
    verification_token: str | None = None


def _repo():
    """按请求构造仓储：走 ``db.backends`` 工厂，与其它路由同一条注册路径。

    工厂内部懒导入 Oracle 实现，构造不做 I/O（连接只在方法被 await 时建立）；
    测试也沿用工厂的 override 模式（``monkeypatch.setattr(backends, ...)``）。
    """
    return backends.get_ai_models_repo()


def _bad(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _clean_text(value: object, *, label: str, limit: int) -> str | None:
    """可空文本列：去空白、空串归 ``None``（「没填」就是 NULL，不是空串）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise _bad(f"{label} 最长 {limit} 个字符")
    if "\r" in text or "\n" in text or "\x00" in text:
        raise _bad(f"{label} 不能包含换行或控制字符")
    return text


def _clean_model_name(value: object) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= MODEL_MAX:
        raise _bad(f"模型名长度必须在 1..{MODEL_MAX} 之间")
    if "\r" in name or "\n" in name or "\x00" in name:
        raise _bad("模型名不能包含换行或控制字符")
    return name


def _clean_fields(payload: ModelUpdateRequest) -> dict[str, Any]:
    """``exclude_unset`` → 只保留调用方真的给了的字段。

    ``None`` 在这里是**显式的清空**（可空列），所以不能用「None = 不改」的过滤：
    只处理 ``model_fields_set`` 里出现过的键。
    """
    given = payload.model_fields_set
    fields: dict[str, Any] = {}

    if "display_name" in given:
        fields["display_name"] = _clean_text(
            payload.display_name, label="展示名", limit=DISPLAY_NAME_MAX
        )
    if "provider" in given:
        fields["provider"] = _clean_text(
            payload.provider, label="渠道", limit=PROVIDER_MAX
        )
    if "notes" in given:
        fields["notes"] = _clean_text(payload.notes, label="备注", limit=NOTES_MAX)
    if "context_length" in given:
        value = payload.context_length
        if value is None:
            fields["context_length"] = None
        elif isinstance(value, bool) or not isinstance(value, int):
            raise _bad("context_length 必须是正整数或 null")
        elif not 1 <= value <= CONTEXT_MAX:
            raise _bad(f"context_length 必须在 1..{CONTEXT_MAX} 之间")
        else:
            fields["context_length"] = value
    if "rate" in given:
        value = payload.rate
        if value is None:
            # 倍率 NOT NULL：响应里不出现比写一个 1 更诚实——但清空不是合法操作。
            raise _bad("rate 不能为空")
        if isinstance(value, bool):
            raise _bad("rate 必须是大于 0 的数字")
        numeric = float(value)
        if not (0 < numeric <= RATE_MAX):
            raise _bad(f"rate 必须大于 0 且不超过 {RATE_MAX:g}")
        fields["rate"] = numeric
    if "enabled" in given:
        value = payload.enabled
        if value is None:
            raise _bad("enabled 必须是布尔值")
        fields["enabled"] = bool(value)
    return fields


async def _catalog(user: dict) -> tuple[dict[str, set[str]], dict[str, int]]:
    """渠道目录：模型 → 渠道名集合、模型 → 上下文窗口（来自凭据配置）。

    ``credentials.list_entries`` 本身就是 admin 全量视图（含 disabled / 已过期的
    渠道）：管理页要看到「这个模型在哪些渠道里配过」，不是「此刻谁能调它」。
    """
    providers: dict[str, set[str]] = {}
    contexts: dict[str, int] = {}
    for view in await credentials.list_entries(user):
        name = str(view.get("name") or "")
        for model in view.get("models") or []:
            if not isinstance(model, str) or not model:
                continue
            providers.setdefault(model, set()).add(name)
        for model, context in (view.get("model_contexts") or {}).items():
            if isinstance(context, int) and not isinstance(context, bool) and context > 0:
                # 同名多渠道时取第一个非空值：与 catalog 的 setdefault 口径一致。
                contexts.setdefault(model, context)
    return providers, contexts


def _shape_row(
    row: Mapping[str, Any] | None,
    *,
    model: str,
    providers: Mapping[str, set[str]],
    contexts: Mapping[str, int],
) -> dict[str, Any]:
    """覆盖行 ∪ 渠道事实 → 响应里的单行。

    ``in_catalog`` = 该模型出现在至少一个渠道里（= 现在真的能被调用）。给自定义
    模型建覆盖行时它是 ``false``——这正是管理页要区分的那一类行。
    ``context_length`` 缺覆盖行时回落到凭据里配的窗口（有就展示，没有就是
    ``None``，不拿 0 顶替）。
    """
    configured = {str(name) for name in providers.get(model, set()) if name}
    data = row or {}
    has_row = bool(row)
    return {
        "model": model,
        "display_name": data.get("display_name"),
        "provider": data.get("provider"),
        "providers": sorted(configured),
        "context_length": (
            data.get("context_length") if has_row else contexts.get(model)
        ),
        "rate": float(data.get("rate") or 1.0),
        "enabled": bool(data.get("enabled", True)),
        "in_catalog": bool(configured),
        "notes": data.get("notes"),
        "updated_at": data.get("updated_at"),
    }


@router.get("")
async def list_models(user=Depends(get_current_user)):
    """全部模型（覆盖行 ∪ 渠道目录），按模型名升序。"""
    _require_admin(user)
    rows = await _repo().list_all()
    providers, contexts = await _catalog(user)
    by_model: dict[str, Mapping[str, Any]] = {
        str(row["model"]): row for row in rows if row.get("model")
    }
    models = [
        _shape_row(
            by_model.get(model),
            model=model,
            providers=providers,
            contexts=contexts,
        )
        for model in sorted(set(by_model) | set(providers))
    ]
    return {"status": "ok", "code": 200, "data": {"models": models}}


@router.put("/{model:path}")
async def update_model(
    model: str,
    payload: ModelUpdateRequest,
    user=Depends(get_current_user),
):
    """按模型名 upsert 覆盖行；模型不在任何渠道里也允许建（自定义模型）。"""
    _require_admin(user)
    name = _clean_model_name(model)
    fields = _clean_fields(payload)
    row = await _repo().upsert(name, fields=fields, actor=user.get("id"))
    # 倍率快照按 60 秒 TTL 缓存，改完必须立刻失效，否则新价最多晚一分钟生效。
    model_rates.invalidate()
    providers, contexts = await _catalog(user)
    return {
        "status": "ok",
        "code": 200,
        "data": _shape_row(row, model=name, providers=providers, contexts=contexts),
    }


@router.post("/discover")
async def discover_models(
    payload: DiscoverRequest,
    user=Depends(get_current_user),
):
    """从上游 ``/models`` 拉取模型元数据（当前重点是上下文窗口）。

    **与其它端点的门禁差异**：本模块其它端点只改展示/计费口径、从不碰密钥，所以
    只挂 ``admin``；这一条会解密凭据里的上游 key 并发真实请求，因此与凭据写路径
    同级——``admin`` + :func:`require_password_verification`（密码再认证）。

    ``apply=False`` 是纯预览：不写库，只回「上游怎么说 / 是否已在凭据里」。
    ``apply=True`` 才回写，且**只动** ``MODELS_JSON`` 里已有模型的 ``context``：
    不新增模型、不改顺序、不超过凭据的模型上限。手填值与上游不一致时以上游为准
    （这正是管理员点「应用」的意图，预览里已经看得到差异）。

    不碰 ``AI_MODELS``：那张表是稀疏覆盖层，``CONTEXT_LENGTH`` 的展示回落逻辑
    保持原样。
    """
    _require_admin(user)
    await require_password_verification(
        payload.verification_token, user, detail="AI_PASSWORD_VERIFICATION_REQUIRED"
    )

    name = str(payload.provider or "").strip()
    if not name:
        raise _bad("渠道名不能为空")

    entry = await credentials.entry_for_name(name)
    if entry is None:
        if name in ai_config.providers():
            # 服务端项来自 settings/env，没有 DB 行可回写；让管理员去改配置文件。
            raise _bad("服务端作用域的渠道不支持回写")
        raise HTTPException(status_code=404, detail="渠道不存在")

    try:
        api_key = await credentials.resolve_api_key(entry)
    except CredentialError:
        raise HTTPException(
            status_code=502, detail="凭据不可用，无法向上游请求模型列表"
        ) from None

    try:
        upstream = await providers.list_models(entry, api_key)
    except RouteError:
        # 只回 host：路径/query 可能带凭据，报文里可能回显请求头。
        host = credentials.host_of_url(entry.get("base_url"))
        raise HTTPException(
            status_code=502,
            detail=f"上游模型列表获取失败（{host}）" if host else "上游模型列表获取失败",
        ) from None

    discovered = {
        row["id"]: row.get("context")
        for row in upstream
        if isinstance(row.get("id"), str) and row["id"]
    }
    configured = set(entry.get("models") or [])
    applied_ids: set[str] = set()

    if payload.apply:
        contexts = entry.get("model_contexts") or {}
        filled: list[str | dict] = []
        for model in entry.get("models") or []:
            context = contexts.get(model)
            upstream_context = discovered.get(model)
            if upstream_context is not None and upstream_context != context:
                context = upstream_context
                applied_ids.add(model)
            # 与原 ``_models_payload`` 同形：没有窗口的仍留字符串，diff 才看得出改了什么。
            filled.append({"id": model, "context": context} if context else model)
        if applied_ids:
            try:
                updated = await backends.get_ai_credentials_repo().update(
                    entry["oid"], models=filled
                )
            except Exception:  # noqa: BLE001 - 写库失败统一翻成 502，不要把栈回给前端
                raise HTTPException(
                    status_code=502, detail="模型上下文回写失败"
                ) from None
            if not updated:
                raise HTTPException(
                    status_code=502, detail="模型上下文回写失败：凭据已不存在"
                )
            # 目录缓存里存的是密文行 + 解析后的 model_contexts，必须立刻失效，
            # 否则新窗口要等 TTL 才被压缩水位看到。
            await credentials.invalidate()
        else:
            applied_ids = set()

    models = [
        {
            "model": row["id"],
            "context_length": row.get("context"),
            "in_credential": row["id"] in configured,
            "applied": row["id"] in applied_ids,
        }
        for row in upstream
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "provider": name,
            "models": models,
            "applied": len(applied_ids),
        },
    }


@router.delete("/{model:path}", status_code=204)
async def delete_model(model: str, user=Depends(get_current_user)):
    """删除覆盖行（幂等：没有这一行也回 204），凭据里的模型不受影响。"""
    _require_admin(user)
    await _repo().delete(_clean_model_name(model))
    # 删覆盖行 = 回到默认倍率 1.0，同样要立刻失效缓存。
    model_rates.invalidate()
    return None


__all__ = ["router"]
