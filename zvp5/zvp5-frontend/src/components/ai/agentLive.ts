import { onScopeDispose, ref, watchEffect, type Ref } from 'vue'
import type {
  AiRunAssistantLive,
  AiRunStreamStatus,
  AiRunToolLive
} from '@/../types/ai'

/**
 * Agent 实时帧适配层（只读消费 store 的流式接口）。
 *
 * store 侧契约（另一车道提供，本层只读、不写 store）：
 *   streamStatus(runId): 'idle' | 'connecting' | 'live' | 'fallback' | 'closed'
 *   liveAssistantFor(runId): AiRunAssistantLive[]   // round_no / stream_key / content（累计值）
 *   liveToolsFor(runId): AiRunToolLive[]             // round_no / tool_call_id / tool_name /
 *                                                      // tool_kind / status / args_summary / result_summary
 *
 * 本层只做三件事：
 *   1) 防御式读取：接口尚未上线（或本次 run 没有实时帧）时返回空，页面照常走已持久化的
 *      消息与事件路径 —— 输入与历史不被阻断。
 *   2) 帧合并：live 工具帧与已持久化事件按「轮次 + 工具名」去重，落库的留痕优先。
 *   3) 渲染节流：高频累计正文用 rAF 合并，一帧最多写一次；不做假打字动画，正文直接
 *      渲染 store 给的累计值。
 */


/** 连接状态：与 store 的只读接口同一份类型，不另立词表。 */
export type AgentStreamStatus = AiRunStreamStatus

/** 一帧助手正文；content 是该流的累计值。 */
export type AgentLiveAssistantFrame = AiRunAssistantLive

/** 一帧工具调用状态。 */
export type AgentLiveToolFrame = AiRunToolLive

/**
 * store 上可选的实时接口。
 *
 * 这里用结构类型而不是直接依赖 store 的类型：接口由另一车道提供，落地前页面要能正常
 * 降级运行（读取时做存在性判断）。
 */
export interface AgentLiveCapableStore {
  streamStatus?: (runId: string) => AgentStreamStatus
  liveAssistantFor?: (runId: string) => AgentLiveAssistantFrame[]
  liveToolsFor?: (runId: string) => AgentLiveToolFrame[]
}

/** 已持久化的一条工具留痕（AiAgentEvent 的最小形状）。 */
export interface AgentPersistedToolEvent {
  round_no: number
  tool_name: string | null
  tool_kind: string | null
  args_summary: string | null
  result_summary: string
  status: string
}

/** 时间线上的一行工具调用（持久化与实时合并后的统一形状）。 */
export interface AgentToolRow {
  /** 展开态与列表 key 共用的稳定标识。 */
  key: string
  round_no: number
  tool_name: string | null
  tool_kind: string | null
  args_summary: string | null
  result_summary: string
  /** 徽标文案 key（沿用仓库既有字典，不新增词条）。 */
  status_label_key: string
  /** 徽标色调类（沿用既有 is-* 语义色）。 */
  status_tone: string
  expanded: boolean
  /** true = 来自实时帧；false = 已落库。 */
  live: boolean
}

/** 工具状态 → 既有字典 key + 既有色调类。 */
const STATUS_PRESETS: Record<string, { label: string; tone: string }> = {
  executed: { label: 'ai.agent.status.executed', tone: 'is-success' },
  proposed: { label: 'ai.agent.status.proposed', tone: 'is-info' },
  denied: { label: 'ai.agent.status.denied', tone: 'is-warning' },
  failed: { label: 'ai.agent.status.failed', tone: 'is-danger' },
  // 实时帧的状态词（pending / success / error）映射到既有词条，不新增文案。
  success: { label: 'ai.agent.status.executed', tone: 'is-success' },
  error: { label: 'ai.agent.status.failed', tone: 'is-danger' },
  pending: { label: 'ai.agent.running', tone: 'is-info' },
  running: { label: 'ai.agent.running', tone: 'is-info' }
}

function statusPreset(status: string): { label: string; tone: string } {
  return STATUS_PRESETS[status] ?? { label: 'ai.agent.running', tone: 'is-info' }
}

/**
 * 合并持久化事件与实时工具帧。
 *
 * 去重规则：同一轮里的同一个工具，落库的那条优先（它带最终状态与结果）；实时帧只在
 * 还没有对应落库留痕时出现。这样轮询把事件补齐后，实时行会自然被落库行顶掉，不会
 * 出现两条同一次调用。
 */
export function mergeToolRows(
  persisted: AgentPersistedToolEvent[],
  live: AgentLiveToolFrame[],
  expandedKeys: readonly string[] = []
): AgentToolRow[] {
  const rows: AgentToolRow[] = persisted.map((event) => {
    const preset = statusPreset(event.status)
    const key = `${event.round_no}|${event.tool_name ?? ''}|${event.result_summary}`
    return {
      key,
      round_no: event.round_no,
      tool_name: event.tool_name,
      tool_kind: event.tool_kind,
      args_summary: event.args_summary,
      result_summary: event.result_summary,
      status_label_key: preset.label,
      status_tone: preset.tone,
      expanded: expandedKeys.includes(key),
      live: false
    }
  })

  const seen = new Set(persisted.map((event) => `${event.round_no}|${event.tool_name ?? ''}`))
  const liveRows: AgentToolRow[] = []
  live.forEach((frame) => {
    const dedupeKey = `${frame.round_no}|${frame.tool_name ?? ''}`
    if (seen.has(dedupeKey)) {
      return
    }
    const preset = statusPreset(frame.status)
    const key = `live|${frame.tool_call_id || `${frame.round_no}|${frame.tool_name ?? ''}`}`
    liveRows.push({
      key,
      round_no: frame.round_no,
      tool_name: frame.tool_name,
      tool_kind: frame.tool_kind,
      args_summary: frame.args_summary,
      result_summary: frame.result_summary ?? '',
      status_label_key: preset.label,
      status_tone: preset.tone,
      expanded: expandedKeys.includes(key),
      live: true
    })
  })

  return [...rows, ...liveRows].sort((a, b) => a.round_no - b.round_no)
}

/** 取最新的累计正文：轮次最大的那一帧；同轮取最后到达的一帧。 */
export function latestAssistantText(frames: AgentLiveAssistantFrame[]): string {
  if (!frames.length) {
    return ''
  }
  let latest = frames[0]
  for (const frame of frames) {
    if (frame.round_no >= latest.round_no) {
      latest = frame
    }
  }
  return latest.content ?? ''
}

/**
 * 订阅某个 run 的实时帧。
 *
 * @param store   只读来源；缺少实时接口时按「无实时帧」处理
 * @param runId   当前 run 的 id（AiRun.oid）；空串表示没有进行中的 run
 */
export function useAgentLive(store: unknown, runId: Ref<string>) {
  const status = ref<AgentStreamStatus>('idle')
  const text = ref('')
  const tools = ref<AgentLiveToolFrame[]>([])

  let frame = 0
  let pendingText = ''
  let pendingTools: AgentLiveToolFrame[] = []

  function cancelFrame() {
    if (!frame) {
      return
    }
    if (typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(frame)
    } else {
      clearTimeout(frame)
    }
    frame = 0
  }

  function flush() {
    frame = 0
    text.value = pendingText
    tools.value = pendingTools
  }

  /** 一帧最多写一次：连续 token 到达时只保留最后一次快照。 */
  function schedule() {
    if (frame) {
      return
    }
    if (typeof requestAnimationFrame === 'function') {
      frame = requestAnimationFrame(flush)
    } else {
      frame = setTimeout(flush, 16) as unknown as number
    }
  }

  watchEffect(() => {
    const id = runId.value
    const live = store as AgentLiveCapableStore
    const readStatus = live?.streamStatus
    if (!id || typeof readStatus !== 'function') {
      status.value = 'idle'
      pendingText = ''
      pendingTools = []
      text.value = ''
      tools.value = []
      cancelFrame()
      return
    }
    status.value = readStatus(id)
    const frames = typeof live.liveAssistantFor === 'function' ? live.liveAssistantFor(id) : []
    pendingText = latestAssistantText(frames)
    pendingTools = typeof live.liveToolsFor === 'function' ? live.liveToolsFor(id) : []
    schedule()
  })

  onScopeDispose(cancelFrame)

  return { status, text, tools }
}
