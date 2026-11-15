"""把模型清单与 Agent Fuel 倍率灌进 ``AI_MODELS``（默认只演算，不写库）。

``AI_MODELS`` 是**稀疏覆盖层**：没有行 = 按 1.0 计费。所以「给全部已知模型建行」
不是数据完整性要求，而是运营要求——让管理页能一眼看到每个模型的倍率与依据，而不
是「没配 = 1.0，但没人知道为什么是 1.0」。

模型清单（键）来自三处的**并集**：

1. ``AI_CREDENTIALS.MODELS_JSON`` 全量（所有 scope：personal / shared）——哪个
   模型真的接进来了；
2. ``settings.AI_SILICONFLOW_FREE_MODELS`` 白名单——运维声明「这些是免费档」；
3. 本脚本内嵌的分数表 / 任务型表键——已知定价但可能还没接进来的模型。

倍率来自内嵌常量表而不是数据库或迁移：

* **为什么倍率不进 DDL 迁移**：倍率是运营参数，会随模型换代、上游降价、平台补贴
  反复调整。写进迁移等于每次调价都要发一次版本、走一次停机窗口，而且改错了只能
  靠又一次迁移回滚。运维数据的正确归属是「运维脚本 + 管理端 editable 的表」。
* 换算口径：``rate = round(score / 56.16, 4)``，基准是 ``THUDM/GLM-Z1-9B-0414``
  （56.16 分 → 1.0000）。基准行的倍率**硬编码**为 1.0000，启动时校验（表里那行
  被改坏就退出 2，而不是悄悄把全表价格平移）。
* 没有智力分的任务型模型（翻译 / 识图 / OCR）按 1.0 占位，``notes`` 记
  ``task-priced: <计价单位>``——它们的真实成本不是 per-token，用 1.0 表示「本脚本
  不替它定价」，而 notes 把「该按什么计价」留给运营读。

**幂等性**：重复跑不会叠加、不会把管理员改过的值冲掉。判定只用「读到的快照 +
本次目标」，写库走 ``upsert`` 的部分更新语义：

* 行不存在 → ``create``（rate / enabled=1 / notes / 渠道名）；
* 行存在且 notes 是**本脚本写的标记**（``seed:ai-models`` / ``task-priced:``）→
  只在 ``rate = 1`` 时补上算出来的倍率（首次落地 / 上次半途中断）；
* 行存在但不是脚本写的（notes 空或管理员手写）→ 默认 ``skip``：管理员把倍率设成
  1.0 是**决定**，不是漏配；
* ``provider`` 只在为空时补（来自「唯一一个配了它的凭据名」），``display_name``
  一律不动。

**--dry-run**：默认行为就是 dry-run？不是——不带任何开关会真写；``--dry-run``
只演算并打印计划，一行不写库、不调 ``invalidate``。

**--force-rate**：把内嵌表里的倍率**强制**写回所有已存在的行（含管理员手改过的
倍率与 notes）。只在「确信表是最新事实、且要覆盖线上手调」时用。

**为什么走 service 层而不是直插 SQL**：倍率有路由层校验（``0 < rate <= 1000``）、
仓储有字段白名单（``MUTABLE_FIELDS``）与「未给的字段保持原值」语义、计费侧还有
一层 60 秒的进程内快照。绕过任何一层都会得到「库里是 A、进程按 B 计费」这种查不
出来的账。所以这里只调 ``backends.get_ai_models_repo().upsert(...)`` +
``model_rates.invalidate()``。

用法（在后端根目录）::

    .venv/bin/python misc/seed_ai_models.py --dry-run
    .venv/bin/python misc/seed_ai_models.py
    .venv/bin/python misc/seed_ai_models.py --force-rate

退出码：0 = 成功（含 dry-run）；2 = 参数错 / 基准行被改坏 / 读库失败。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# 以 ``python misc/seed_ai_models.py`` 运行时 ``sys.path[0]`` 是 ``misc/``，
# ``services`` / ``db`` 都不可见；把后端根放进来才能复用运行时那套服务层。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from db import backends  # noqa: E402

from services.ai import config as ai_config  # noqa: E402
from services.ai import credentials, model_rates  # noqa: E402

# ---------------------------------------------------------------------------
# 定价事实（内嵌常量，不来自数据库 / 迁移）
# ---------------------------------------------------------------------------

#: 基准模型与基准分：rate = 1.0000 的定义点。改这两个值等于重新定价全表，
#: 所以启动时校验「表里基准行还在、分数没被改坏」。
BASE_MODEL = "THUDM/GLM-Z1-9B-0414"
BASE_SCORE = 56.16
BASE_RATE = 1.0

#: 倍率小数位：``AI_MODELS.RATE`` 是 ``NUMBER(10,4)``，多给的小数位落库会被截断，
#: 不如在脚本里就按同一精度取整，避免「打印 1.21705，库里 1.2171」。
RATE_DIGITS = 4

#: 路由层的倍率上限（``routers/ai_models_router.RATE_MAX``）。这里再挡一次：
#: 脚本直接调服务层，绕过路由校验时需要一道同口径的闸。
RATE_MAX = 1000.0

#: 内置表写进 ``notes`` 的前缀：**幂等判定的唯一依据**。改这个前缀 = 下一次跑
#: 会把所有旧行当成「管理员手写的」从而全部 skip。
SEED_NOTE_PREFIX = "seed:ai-models"
TASK_NOTE_PREFIX = "task-priced:"

#: ``notes`` 列宽（``routers/ai_models_router.NOTES_MAX``）。
NOTES_MAX = 400
#: ``provider`` 列宽（同上 ``PROVIDER_MAX``）：凭据名可以更长，超了就不填。
PROVIDER_MAX = 40

#: 复合分 = 0.4×GPQA-D + 0.35×AIME + 0.25×LCB（厂商 model card 口径）。
#: 采集日期 2026-10-30。``mapped:`` = 第三方渠道名映射到官方模型后借用官方分数。
SCORE_TABLE: dict[str, tuple[float, str]] = {
    # model-card：官方 model card 三项还原成同一复合分
    BASE_MODEL: (BASE_SCORE, "model-card"),
    "Qwen/Qwen3.5-4B": (68.35, "model-card"),
    "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B": (66.27, "model-card"),
    "Qwen/Qwen3-8B": (64.47, "model-card"),
    "THUDM/GLM-Z1-32B-0414": (63.47, "model-card"),
    "THUDM/GLM-4-9B-0414": (45.00, "model-card"),
    "Qwen/Qwen2.5-7B-Instruct": (33.44, "model-card"),
    # estimated：多模态 / 三方榜单口径不一致，按可比项折算
    "Qwen/Qwen3-VL-8B-Instruct": (64.00, "estimated"),
    "XingChenAGI/Xing4.0-29B": (62.00, "estimated"),
    # mapped：渠道别名 → 官方模型，分数借官方
    "gpt-5.4": (88.00, "mapped:GPT-5.4"),
    "deepseek-flash": (86.00, "mapped:DeepSeek V4.1 Flash"),
}

#: 任务型 / 专用模型：真实成本不是 per-token，rate 写 1.0 占位，notes 记计价单位。
TASK_TABLE: dict[str, str] = {
    "tencent/Hunyuan-MT-7B": "per-1k-chars",
    "Kev-4B": "per-call",
    "SemIf": "per-call",
    "deepseek-ai/DeepSeek-OCR": "per-page",
    "PaddlePaddle/PaddleOCR-VL-1.5": "per-page",
}

#: 计费方式（打印用）：分数表 = per-1k-tokens，任务表 = 自己的单位。
BILLING_TOKENS = "per-1k-tokens"


class SeedError(Exception):
    """可预期的失败：打一行原因、退出码 2，不打 traceback。"""


# ---------------------------------------------------------------------------
# 换算
# ---------------------------------------------------------------------------


def rate_from_score(score: object) -> float:
    """智力分 → 倍率：``round(score / 56.16, 4)``，非法分直接失败。

    非法输入**不**兜回 1.0：兜底会让一行填错的分数变成「按基准价计费」，账上看
    不出来。宁可退出 2 让人修表。
    """
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise SeedError(f"分数必须是数字: {score!r}")
    value = float(score)
    if not value > 0:
        raise SeedError(f"分数必须大于 0: {value!r}")
    rate = round(value / BASE_SCORE, RATE_DIGITS)
    if not 0 < rate <= RATE_MAX:
        raise SeedError(f"算出的倍率越界（0..{RATE_MAX:g}）: {rate!r}")
    return rate


def assert_base_rate() -> None:
    """启动校验：基准行的分数与倍率必须还是 1.0000 的那个定义点。

    表里的基准行被改成别的分数（或多加了小数位）会让**全表**倍率一起平移，而
    输出仍然「看起来很正常」。这类错误只能靠断言拦。
    """
    entry = SCORE_TABLE.get(BASE_MODEL)
    if entry is None:
        raise SeedError(f"分数表里没有基准模型 {BASE_MODEL}")
    score, _source = entry
    if score != BASE_SCORE:
        raise SeedError(
            f"基准模型 {BASE_MODEL} 的分数被改成 {score!r}（应为 {BASE_SCORE}）"
        )
    if rate_from_score(score) != BASE_RATE:
        raise SeedError(
            f"基准模型 {BASE_MODEL} 换算出的倍率不是 {BASE_RATE:.4f}：{rate_from_score(score)!r}"
        )


def score_note(model: str) -> str:
    """分数行的 ``notes``：带分数与来源，且以 :data:`SEED_NOTE_PREFIX` 开头。"""
    score, source = SCORE_TABLE[model]
    note = f"{SEED_NOTE_PREFIX} score={score:.2f} src={source}"
    return note[:NOTES_MAX]


def task_note(model: str) -> str:
    return f"{TASK_NOTE_PREFIX} {TASK_TABLE[model]}"[:NOTES_MAX]


# ---------------------------------------------------------------------------
# 目标（模型 → 应得的倍率）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """一个模型的定价意图（与库里现状无关）。"""

    model: str
    rate: float
    score: float | None
    billing: str
    notes: str | None

    @property
    def is_priced(self) -> bool:
        return self.score is not None or self.notes is not None


def target_for(model: str) -> Target | None:
    """内置表 → 目标；表里没有（也没有分数）的模型返回 ``None``。"""
    if model in SCORE_TABLE:
        score, _source = SCORE_TABLE[model]
        return Target(
            model=model,
            rate=rate_from_score(score),
            score=score,
            billing=BILLING_TOKENS,
            notes=score_note(model),
        )
    if model in TASK_TABLE:
        return Target(
            model=model,
            rate=1.0,
            score=None,
            billing=f"{TASK_NOTE_PREFIX} {TASK_TABLE[model]}",
            notes=task_note(model),
        )
    return None


def catalog_models(
    credential_rows: Sequence[Mapping[str, Any]] | None,
    whitelist: Iterable[str] | None,
) -> tuple[list[str], dict[str, str]]:
    """三处并集 → ``(模型名列表, 模型 → 唯一渠道名)``。

    渠道名只在**恰好一个凭据**配过该模型时给出：多渠道同名时随便挑一个会把
    「这个模型属于谁」写成假的（倍率按裸名命中，与渠道无关，所以留空无害）。
    """
    names: list[str] = []
    providers: dict[str, set[str]] = {}

    def _add(name: object, provider: str | None = None) -> None:
        model = str(name or "").strip()
        if not model:
            return
        if model not in names:
            names.append(model)
        if provider:
            providers.setdefault(model, set()).add(provider)

    for row in credential_rows or []:
        if not isinstance(row, Mapping):
            continue
        entry_name = str(row.get("name") or "").strip()
        for item in _model_ids(row.get("models")):
            _add(item, entry_name or None)

    for model in whitelist or []:
        _add(model)

    for model in (*SCORE_TABLE, *TASK_TABLE):
        _add(model)

    hints = {
        model: next(iter(owners))
        for model, owners in providers.items()
        if len(owners) == 1 and len(next(iter(owners))) <= PROVIDER_MAX
    }
    # 排序只影响输出可读性；同名模型的键本来就唯一。
    return sorted(names, key=lambda name: (name.lower(), name)), hints


def _model_ids(value: object) -> list[str]:
    """``MODELS_JSON`` 的两种形态都要认（``["m"]`` / ``[{"id": "m"}]``）。

    直接复用凭据服务的口径：它已经是「读库口径」的唯一实现，脚本再抄一份迟早漂。
    """
    try:
        return credentials.model_ids(value)
    except Exception:  # noqa: BLE001 - 脏数据不该让整个脚本停下
        return []


# ---------------------------------------------------------------------------
# 计划（目标 + 现状 → create/update/skip）
# ---------------------------------------------------------------------------

CREATE = "create"
UPDATE = "update"
SKIP = "skip"


@dataclass(frozen=True)
class PlannedRow:
    """一行落库计划：``fields`` 为空即 skip。"""

    model: str
    score: float | None
    rate: float
    billing: str
    action: str
    reason: str
    fields: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        score = f"{self.score:6.2f}" if self.score is not None else "     -"
        return (
            f"{self.model} | {score} | {self.rate:.4f} | "
            f"{self.billing} | {self.action} ({self.reason})"
        )


def _is_seed_notes(notes: object) -> bool:
    """``notes`` 是不是**本脚本**写的（幂等判定的唯一依据）。"""
    text = str(notes or "").strip()
    return text.startswith(SEED_NOTE_PREFIX) or text.startswith(TASK_NOTE_PREFIX)


def _rate_of(row: Mapping[str, Any]) -> float:
    value = row.get("rate")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return model_rates.DEFAULT_RATE
    return float(value)


def plan_row(
    target: Target | None,
    existing: Mapping[str, Any] | None,
    *,
    provider_hint: str | None = None,
    force_rate: bool = False,
) -> PlannedRow:
    """单个模型的落库计划（纯函数，不碰库）。

    判定顺序与 docstring 里的幂等规则一一对应：

    * 没有定价知识（不在内置表里）→ ``skip``，理由 ``no-price``。**不**建一行
      rate=1.0：稀疏覆盖层里「有行 = 有人表过态」，凭空造行只会让管理页多出一堆
      没有依据的 1.0；
    * 没有现状行 → ``create``；
    * 有现状行 → 只补空值 / 只补脚本自己留下的坑，其余 ``skip``。
    """
    model = (target.model if target is not None else "")
    if target is None:
        return PlannedRow(
            model=model,
            score=None,
            rate=model_rates.DEFAULT_RATE,
            billing="-",
            action=SKIP,
            reason="no-price",
        )

    if existing is None:
        fields: dict[str, Any] = {
            "rate": target.rate,
            "enabled": 1,
            "notes": target.notes,
        }
        if provider_hint:
            fields["provider"] = provider_hint
        return PlannedRow(
            model=target.model,
            score=target.score,
            rate=target.rate,
            billing=target.billing,
            action=CREATE,
            reason="missing",
            fields=fields,
        )

    current_rate = _rate_of(existing)
    seed_owned = _is_seed_notes(existing.get("notes"))
    changes: dict[str, Any] = {}
    reasons: list[str] = []

    if provider_hint and not str(existing.get("provider") or "").strip():
        changes["provider"] = provider_hint
        reasons.append("provider:empty")

    if force_rate:
        if current_rate != target.rate:
            changes["rate"] = target.rate
            reasons.append("rate:forced")
        # notes 只在「脚本自己的」或空的时候刷新：管理员手写的说明留着。
        if seed_owned or not str(existing.get("notes") or "").strip():
            if target.notes and str(existing.get("notes") or "").strip() != target.notes:
                changes["notes"] = target.notes
                reasons.append("notes:refresh")
    elif current_rate == model_rates.DEFAULT_RATE and seed_owned:
        # 上次跑到一半 / 首次落地时 rate 还是占位值：补齐。管理员手改成 1.0 的行
        # 不是 seed 标记，走不到这里。
        if target.rate != current_rate:
            changes["rate"] = target.rate
            reasons.append("rate:fill")
        if target.notes and str(existing.get("notes") or "").strip() != target.notes:
            changes["notes"] = target.notes
            reasons.append("notes:refresh")

    if not changes:
        reason = "manual" if not seed_owned else "up-to-date"
        return PlannedRow(
            model=target.model,
            score=target.score,
            rate=current_rate,
            billing=target.billing,
            action=SKIP,
            reason=reason,
        )

    return PlannedRow(
        model=target.model,
        score=target.score,
        rate=target.rate,
        billing=target.billing,
        action=UPDATE,
        reason=",".join(reasons),
        fields=changes,
    )


def build_plan(
    models: Sequence[str],
    existing_rows: Sequence[Mapping[str, Any]] | None,
    *,
    provider_hints: Mapping[str, str] | None = None,
    force_rate: bool = False,
) -> list[PlannedRow]:
    """全量计划：``models`` 决定行集合，``existing_rows`` 决定每行的动作。"""
    current = {
        str(row.get("model")): row
        for row in existing_rows or []
        if isinstance(row, Mapping)
    }
    hints = provider_hints or {}
    return [
        plan_row(
            target_for(model),
            current.get(model),
            provider_hint=hints.get(model),
            force_rate=force_rate,
        )
        for model in models
    ]


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def summarise(plan: Sequence[PlannedRow]) -> dict[str, int]:
    counts = {CREATE: 0, UPDATE: 0, SKIP: 0}
    for row in plan:
        counts[row.action] = counts.get(row.action, 0) + 1
    return counts


def print_plan(plan: Sequence[PlannedRow]) -> None:
    print("model | score | rate | billing | action")
    for row in plan:
        print(row.line())


async def collect() -> tuple[list[str], dict[str, str], list[dict]]:
    """读凭据目录与现有覆盖行 → ``(模型并集, 渠道提示, 现有行)``。

    读失败一律抛 :class:`SeedError`：脚本的价值就是「把现状和目标对齐」，读不到
    现状时继续跑只会打印一份假的计划。
    """
    try:
        credential_rows = await backends.get_ai_credentials_repo().list_all()
    except Exception as exc:  # noqa: BLE001 - 统一翻译成退出码 2
        raise SeedError(f"读 AI_CREDENTIALS 失败: {type(exc).__name__}") from None

    models, hints = catalog_models(credential_rows, ai_config.siliconflow_free_models())

    try:
        existing = await backends.get_ai_models_repo().list_all()
    except Exception as exc:  # noqa: BLE001
        raise SeedError(f"读 AI_MODELS 失败: {type(exc).__name__}") from None

    return models, hints, list(existing)


async def apply_plan(plan: Sequence[PlannedRow], *, actor: str) -> int:
    """按计划写库（只写 create/update），最后让倍率快照立刻失效。

    逐行 ``upsert`` 而不是攒一个事务：每行都是独立的运营事实，中途失败时已经写成
    的行保留（下一次跑会按 ``rate:fill`` 补齐剩下的），比一个长事务整批回滚更好
    排查。
    """
    repo = backends.get_ai_models_repo()
    written = 0
    for row in plan:
        if row.action == SKIP or not row.fields:
            continue
        await repo.upsert(row.model, fields=row.fields, actor=actor)
        written += 1
    if written:
        # 计费读的是进程内 60 秒快照：不失效的话，线上要再按旧价跑一分钟。
        model_rates.invalidate()
    return written


# ---------------------------------------------------------------------------
# 参数与主流程
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把模型清单与 AF 倍率灌进 AI_MODELS（默认真写，--dry-run 只演算）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 2 参数错、基准行被改坏、读库失败",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="只打印计划，不写库也不失效倍率缓存",
    )
    parser.add_argument(
        "--force-rate",
        action="store_true",
        dest="force_rate",
        help="用内嵌表强制覆盖已存在行的倍率（含管理员手改过的值）",
    )
    return parser.parse_args(argv)


async def _execute(args: argparse.Namespace) -> int:
    models, hints, existing = await collect()
    plan = build_plan(
        models, existing, provider_hints=hints, force_rate=args.force_rate
    )

    print_plan(plan)
    counts = summarise(plan)
    print(
        f"合计 {len(plan)} 行: create={counts[CREATE]} "
        f"update={counts[UPDATE]} skip={counts[SKIP]}"
    )

    if args.dry_run:
        print("dry-run：未写库（同一命令去掉 --dry-run 即落库）")
        return 0

    actor = _actor()
    written = await apply_plan(plan, actor=actor)
    print(f"已写库 {written} 行（updated_by={actor}）")
    return 0


def _actor() -> str:
    """``updated_by``：与 ``misc/seed_channel.py`` 同法——共享哨兵 + admin 形状。

    平台级配置（倍率）不属于任何真人管理员，责任人是「运维脚本」，所以用共享
    哨兵 OID 而不是某个账号的 id（那个人离职/改名后审计就断了）。
    """
    return str(credentials.SHARED_OWNER)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        assert_base_rate()
        return asyncio.run(_execute(args))
    except SeedError as exc:
        print(f"seed_ai_models: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
