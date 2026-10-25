<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ElButton,
  ElCard,
  ElEmpty,
  ElIcon,
  ElInput,
  ElMessage,
  ElOption,
  ElSegmented,
  ElSelect,
  ElSwitch,
  ElTag,
  ElTooltip
} from 'element-plus'
import { CopyDocument, Delete, Promotion, VideoPause, WarningFilled } from '@element-plus/icons-vue'
import { useWindowSize } from '@vueuse/core'
import {
  AiRequestError,
  aiErrorCopy,
  aiErrorSection,
  confirmProposal,
  createChatCompletion,
  fetchPendingProposals,
  fetchRun,
  listModels,
  readCompletionText,
  rejectProposal,
  runAgent,
  streamChatCompletion
} from '@/api/ai'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import type { AiChatMessage, AiModelItem } from '@/../types/ai'
import { buildChatRequest, turnsToMessages, type ChatTurn } from './chatSession'
import ZAiProposalCard from './ZAiProposalCard.vue'
import {
  eventStatusTone,
  markDualAuthCached,
  normalizeEventViews,
  normalizeProposals,
  normalizeStopReason,
  normalizeUsage,
  stopReasonHintKey,
  stopReasonKey,
  toolLabelKey,
  type AgentEventView,
  type AgentProposalCard,
  type AgentProposalState,
  type PageTurn
} from './agentSession'

const { t } = useI18n()
const { height } = useWindowSize()

/** 审计场景名：后端把它写进 AI_CALL_LOGS.PURPOSE（只用于统计，不影响额度）。 */
const AI_SCENE_CHAT = 'chat'

/** 提案有效期的刷新间隔：后端按 10 分钟计，30 秒刷一次足够。 */
const PROPOSAL_TICK_MS = 30_000

/** 对话 / 智能体：智能体走 /ai/agent/run，一次返回全部结果，不流式。 */
const mode = ref('chat')
const modeOptions = computed(() => [
  { label: t('ai.chat.modes.chat'), value: 'chat' },
  { label: t('ai.chat.modes.agent'), value: 'agent' }
])
const isAgentMode = computed(() => mode.value === 'agent')

const models = ref<AiModelItem[]>([])
const modelsLoading = ref(false)
const modelsFailed = ref(false)
const model = ref('')
const streamEnabled = ref(true)

const turns = ref<PageTurn[]>([])
const input = ref('')
const sending = ref(false)
const cooldown = ref(0)

/** 待确认提案：进入智能体模式时拉一次；刷新页面后仍能看到没处理的写操作。 */
const pendingProposals = ref<AgentProposalCard[]>([])
const pendingLoading = ref(false)
const pendingFailed = ref(false)
const pendingOpen = ref(false)

/** 双人动态码弹窗（复用系统页那一个，运维写操作要另一位管理员放行）。 */
const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

let controller: AbortController | null = null
let cooldownTimer: ReturnType<typeof setInterval> | null = null
let proposalTimer: ReturnType<typeof setInterval> | null = null
let turnSeq = 0

/** 提案倒计时用的「当前时间」：只在有 pending 提案时按 30 秒推进。 */
const proposalNow = ref(Date.now())

const listHeight = computed(() => Math.max(220, Math.round(height.value * 0.52)))
const hasModels = computed(() => models.value.length > 0)
const canSend = computed(
  () => hasModels.value && Boolean(model.value) && !sending.value && cooldown.value <= 0
)
/** 待确认提案数：会话内的提案 + 顶部待确认入口的提案。 */
const pendingCount = computed(
  () =>
    pendingProposals.value.filter((card) => card.state === 'pending').length +
    turns.value.reduce(
      (total, turn) =>
        total + (turn.proposals?.filter((card) => card.state === 'pending').length ?? 0),
      0
    )
)

async function loadModels() {
  modelsLoading.value = true
  modelsFailed.value = false
  try {
    const result = await listModels()
    const list = Array.isArray(result?.data) ? result.data : []
    models.value = list
    if (!model.value && list.length > 0) {
      model.value = list[0]!.id
    }
    if (list.length === 0) {
      model.value = ''
    }
  } catch {
    models.value = []
    modelsFailed.value = true
  } finally {
    modelsLoading.value = false
  }
}

function nextTurnId(): number {
  turnSeq += 1
  return turnSeq
}

function startCooldown(seconds: number) {
  cooldown.value = Math.max(1, Math.ceil(seconds))
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
  }
  cooldownTimer = setInterval(() => {
    cooldown.value -= 1
    if (cooldown.value <= 0) {
      cooldown.value = 0
      if (cooldownTimer) {
        clearInterval(cooldownTimer)
        cooldownTimer = null
      }
    }
  }, 1000)
}

function stopCooldown() {
  if (cooldownTimer) {
    clearInterval(cooldownTimer)
    cooldownTimer = null
  }
  cooldown.value = 0
}

function pendingProposalCount(): number {
  return pendingCount.value
}

// 有待确认提案才开定时器：没有提案时页面不该常驻一个空转的 interval。
function syncProposalTimer() {
  const active = pendingProposalCount() > 0
  if (active && !proposalTimer) {
    proposalNow.value = Date.now()
    proposalTimer = setInterval(() => {
      proposalNow.value = Date.now()
    }, PROPOSAL_TICK_MS)
  } else if (!active && proposalTimer) {
    clearInterval(proposalTimer)
    proposalTimer = null
  }
}

function stopProposalTimer() {
  if (proposalTimer) {
    clearInterval(proposalTimer)
    proposalTimer = null
  }
}

/**
 * 拉本人待确认的提案。
 *
 * 页面刷新后对话记录没了，但没处理的写操作还在服务端——靠这个入口找回来。
 */
async function loadPendingProposals() {
  pendingLoading.value = true
  pendingFailed.value = false
  try {
    const result = await fetchPendingProposals('proposed')
    pendingProposals.value = normalizeProposals(result?.proposals)
  } catch {
    pendingProposals.value = []
    pendingFailed.value = true
  } finally {
    pendingLoading.value = false
    syncProposalTimer()
  }
}

function toggleEvent(event: AgentEventView) {
  event.expanded = !event.expanded
}

function markError(turn: ChatTurn, error: unknown) {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const copy = aiErrorCopy(code, t)
  turn.status = 'error'
  turn.errorCode = code ?? ''
  turn.errorTitle = copy.title
  turn.errorHint = copy.hint
  turn.requestId = aiError?.requestId ?? null
  turn.quotaUsed = aiError?.quotaUsed ?? null
  turn.quotaLimit = aiError?.quotaLimit ?? null
  if (code === 'AI_RATE_LIMITED' && aiError?.retryAfter) {
    startCooldown(aiError.retryAfter)
  }
}

const listRef = ref<HTMLElement>()

// 流式 delta 忠实度 vs 帧开销：SSE 几百个 chunk 若逐个写 DOM + 读 scrollHeight，
// 每秒几十次强制同步布局。这里把 delta 攒到缓冲，rAF 每帧合并冲刷一次。
let pendingDelta = ''
let flushFrame = 0
let activeTurn: ChatTurn | null = null
let autoScroll = true

function listElement(): HTMLElement | undefined {
  return listRef.value
}

// 用户手动上翻时暂停自动跟随，滚回底部附近（64px 容差）恢复
function handleListScroll() {
  const element = listElement()
  if (!element) return
  autoScroll = element.scrollTop + element.clientHeight >= element.scrollHeight - 64
}

function scrollToBottom() {
  const element = listElement()
  if (!element || !autoScroll) return
  element.scrollTop = element.scrollHeight
}

function cancelFlush() {
  if (flushFrame) {
    cancelAnimationFrame(flushFrame)
    flushFrame = 0
  }
}

function applyBufferedDelta() {
  if (!activeTurn || !pendingDelta) return
  activeTurn.content += pendingDelta
  pendingDelta = ''
  void nextTick(scrollToBottom)
}

function scheduleDeltaFlush() {
  if (flushFrame) return
  if (typeof requestAnimationFrame !== 'function') {
    applyBufferedDelta()
    return
  }
  flushFrame = requestAnimationFrame(() => {
    flushFrame = 0
    applyBufferedDelta()
  })
}

// 流结束 / 中断 / 报错时同步冲刷，保证终态立即可见（也让 vite 测试不必等帧）
function flushDeltaNow() {
  cancelFlush()
  applyBufferedDelta()
}

async function send() {
  if (!canSend.value) {
    return
  }
  const text = input.value.trim()
  if (!text) {
    return
  }

  // 模式在发送瞬间定格：中途切回对话页不该把这一轮的执行路径也换掉。
  const turnMode = isAgentMode.value ? 'agent' : 'chat'

  const userTurn: PageTurn = {
    id: nextTurnId(),
    role: 'user',
    content: text,
    status: 'done',
    mode: turnMode
  }
  turns.value.push(userTurn)

  const request = buildChatRequest(turnsToMessages(turns.value), model.value, streamEnabled.value)
  turns.value.push({
    id: nextTurnId(),
    role: 'assistant',
    content: '',
    status: 'pending',
    mode: turnMode
  })
  // 取响应式代理再改字段，否则改的是原始对象、视图不重渲染
  const assistantTurn = turns.value[turns.value.length - 1]!

  input.value = ''
  sending.value = true
  autoScroll = true
  void nextTick(scrollToBottom)

  if (turnMode === 'agent') {
    await runAgentTurn(assistantTurn, request.messages)
  } else if (streamEnabled.value) {
    controller = new AbortController()
    activeTurn = assistantTurn
    try {
      const result = await streamChatCompletion({
        model: request.model,
        messages: request.messages,
        signal: controller.signal,
        scene: AI_SCENE_CHAT,
        onDelta: (delta) => {
          pendingDelta += delta
          scheduleDeltaFlush()
        }
      })
      flushDeltaNow()
      assistantTurn.content = result.text || assistantTurn.content
      assistantTurn.requestId = result.requestId
      assistantTurn.status = result.interrupted ? 'interrupted' : 'done'
    } catch (error) {
      flushDeltaNow()
      if ((error as { name?: string })?.name === 'AbortError') {
        assistantTurn.status = 'stopped'
      } else {
        markError(assistantTurn, error)
      }
    } finally {
      activeTurn = null
      pendingDelta = ''
      cancelFlush()
      controller = null
    }
  } else {
    try {
      const result = await createChatCompletion(request.model, request.messages, AI_SCENE_CHAT)
      assistantTurn.content = readCompletionText(result)
      assistantTurn.status = 'done'
    } catch (error) {
      markError(assistantTurn, error)
    }
  }

  sending.value = false
  void nextTick(scrollToBottom)
}

/**
 * 智能体模式的一次 run。
 *
 * 后端一次返回全部结果（不推流）：等待期间只有一个「正在执行工具」的加载态，
 * 回来后把 events 渲染成时间线、proposals 渲染成确认卡。
 */
async function runAgentTurn(turn: PageTurn, messages: AiChatMessage[]) {
  controller = new AbortController()
  try {
    const result = await runAgent(messages, model.value || undefined, controller.signal)
    turn.content = typeof result?.content === 'string' ? result.content : ''
    turn.events = normalizeEventViews(result?.events)
    turn.proposals = normalizeProposals(result?.proposals)
    turn.requestId = typeof result?.request_id === 'string' ? result.request_id : null
    // 触顶/超时不是错误：用提示条说明「为什么停」，内容照常给用户。
    turn.stopReason = normalizeStopReason(result?.stop_reason)
    turn.rounds = typeof result?.rounds === 'number' ? result.rounds : 0
    turn.usage = normalizeUsage(result?.usage)
    turn.status = 'done'
  } catch (error) {
    if ((error as { name?: string })?.name === 'AbortError') {
      turn.status = 'stopped'
    } else {
      markError(turn, error)
    }
  } finally {
    controller = null
    syncProposalTimer()
  }
}

/**
 * 回看一次 run 的工具留痕。
 *
 * 服务端按 request_id 取回时间线（刷新页面后对话没了，留痕还在）。
 */
async function toggleReplay(turn: PageTurn) {
  if (turn.replay) {
    turn.replay = null
    return
  }
  if (!turn.requestId) {
    return
  }
  turn.replayLoading = true
  turn.replayFailed = false
  try {
    const result = await fetchRun(turn.requestId)
    turn.replay = normalizeEventViews(result?.events)
  } catch {
    turn.replayFailed = true
    turn.replay = null
  } finally {
    turn.replayLoading = false
  }
}

/** 提案错误的文案：已知错误码沿用全局映射，未知的按状态码给明确说法。 */
function proposalErrorCopy(code: string | null, status: number | null) {
  if (code === 'AI_AGENT_TOTP_REQUIRED') {
    return {
      title: t('ai.agent.proposalErrors.totpRequired'),
      hint: t('ai.agent.proposalErrors.totpRequiredHint')
    }
  }
  if (aiErrorSection(code) !== 'UNKNOWN') {
    return aiErrorCopy(code, t)
  }
  switch (status) {
    case 403:
      return {
        title: t('ai.agent.proposalErrors.forbidden'),
        hint: t('ai.agent.proposalErrors.forbiddenHint')
      }
    case 409:
      return {
        title: t('ai.agent.proposalErrors.conflict'),
        hint: t('ai.agent.proposalErrors.conflictHint')
      }
    case 410:
      return {
        title: t('ai.agent.proposalErrors.expired'),
        hint: t('ai.agent.proposalErrors.expiredHint')
      }
    case 422:
      return {
        title: t('ai.agent.proposalErrors.totpRequired'),
        hint: t('ai.agent.proposalErrors.totpRequiredHint')
      }
    default:
      return {
        title: t('ai.agent.proposalErrors.generic'),
        hint: t('ai.agent.proposalErrors.genericHint')
      }
  }
}

function applyProposalError(card: AgentProposalCard, error: unknown) {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const copy = proposalErrorCopy(code, aiError?.status ?? null)
  card.errorCode = code ?? ''
  card.errorTitle = copy.title
  card.errorHint = copy.hint
  card.requestId = aiError?.requestId ?? null
}

/** 提案失败的终态：过期单独落一个，其余算处理失败。 */
function proposalErrorState(error: unknown): AgentProposalState {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const status = aiError?.status ?? null
  if (code === 'AI_AGENT_PROPOSAL_EXPIRED' || status === 410) {
    return 'expired'
  }
  return 'error'
}

/** 动态码类的失败可以原样重试：留在待确认态，让用户换个码再来一次。 */
function isAuthFailure(error: unknown): boolean {
  const aiError = error instanceof AiRequestError ? error : null
  const code = aiError?.code ?? null
  const status = aiError?.status ?? null
  return code === 'AI_AGENT_TOTP_REQUIRED' || status === 403 || status === 422
}

/**
 * 仍需二次验证（422 AI_AGENT_TOTP_REQUIRED）。
 *
 * 30 分钟授权窗口刚好过期时会出现：这不是失败，只是要再要一次码。
 */
function isTotpRequired(error: unknown): boolean {
  const aiError = error instanceof AiRequestError ? error : null
  return aiError?.code === 'AI_AGENT_TOTP_REQUIRED'
}

/** 打开动态码弹窗；用户取消返回 null。 */
async function requestTotp(card: AgentProposalCard): Promise<string | null> {
  let dialog = authRef.value
  if (!dialog) {
    await nextTick()
    dialog = authRef.value
  }
  if (!dialog) {
    ElMessage.error(t('ai.agent.dualAuth.unavailable'))
    return null
  }
  const code = await dialog.open(
    t('ai.agent.dualAuth.scene', { tool: t(toolLabelKey(card.toolName)) })
  )
  if (!code) {
    ElMessage.warning(t('ai.agent.dualAuth.cancelled'))
  }
  return code
}

/** 确认最多试三次：错的码不该把人锁在弹层里。 */
const MAX_CONFIRM_ATTEMPTS = 3

/**
 * 确认一条提案。
 *
 * 运维写操作（文件 / 数据库 / SSH）要另一位管理员放行：第一次弹动态码框，拿到码再
 * 带码确认。授权在 30 分钟窗口内有效——命中缓存（dual_auth_cached）时直接确认、不再
 * 弹框；窗口刚好过期时服务端回 422 AI_AGENT_TOTP_REQUIRED，这时重新弹码，不当失败。
 */
async function confirmCard(card: AgentProposalCard) {
  if (card.state !== 'pending') {
    return
  }
  let needCode = card.requiresDualAuth && !card.dualAuthCached
  for (let attempt = 0; attempt < MAX_CONFIRM_ATTEMPTS; attempt += 1) {
    let totpCode: string | undefined
    if (needCode) {
      // 弹层开着时按钮禁用，避免重复打开同一个弹层。
      card.state = 'confirming'
      const code = await requestTotp(card)
      if (!code) {
        card.state = 'pending'
        return
      }
      totpCode = code
    }
    card.state = 'confirming'
    try {
      const result =
        totpCode === undefined
          ? await confirmProposal(card.oid)
          : await confirmProposal(card.oid, totpCode)
      authRef.value?.succeed()
      card.state = 'confirmed'
      card.resultSummary = typeof result?.result_summary === 'string' ? result.result_summary : ''
      if (card.requiresDualAuth) {
        // 这一次授权已生效：同列表里其余待确认的运维写卡片在窗口内都免码。
        applyDualAuthCache()
      }
      ElMessage.success(t('ai.agent.proposal.confirmedNotice'))
      return
    } catch (error) {
      if (isTotpRequired(error)) {
        // 授权窗口刚好过期：重新弹码要一次，别把它显示成失败。
        needCode = true
        card.state = 'pending'
        card.errorCode = ''
        card.errorTitle = ''
        card.errorHint = ''
        continue
      }
      applyProposalError(card, error)
      if (totpCode !== undefined && isAuthFailure(error)) {
        const message = `${card.errorTitle}：${card.errorHint}`
        authRef.value?.fail(message)
        ElMessage.error(message)
        card.state = 'pending'
        continue
      }
      card.state = proposalErrorState(error)
      return
    } finally {
      syncProposalTimer()
      void nextTick(scrollToBottom)
    }
  }
}

/**
 * 首次双人授权成功后，把当前列表里其余待确认的运维写卡片标记为免码。
 *
 * 覆盖会话内的提案卡与顶部待确认入口两处。
 */
function applyDualAuthCache() {
  for (const turn of turns.value) {
    if (turn.proposals) {
      markDualAuthCached(turn.proposals)
    }
  }
  markDualAuthCached(pendingProposals.value)
}

async function rejectCard(card: AgentProposalCard) {
  if (card.state !== 'pending') {
    return
  }
  card.state = 'rejecting'
  try {
    await rejectProposal(card.oid)
    card.state = 'rejected'
    ElMessage.success(t('ai.agent.proposal.rejectedNotice'))
  } catch (error) {
    applyProposalError(card, error)
    card.state = proposalErrorState(error)
  } finally {
    syncProposalTimer()
  }
}

function stop() {
  controller?.abort()
  controller = null
}

function clearSession() {
  controller?.abort()
  controller = null
  activeTurn = null
  pendingDelta = ''
  cancelFlush()
  autoScroll = true
  turns.value = []
  stopCooldown()
  stopProposalTimer()
  ElMessage.success(t('ai.chat.cleared'))
}

async function copyValue(value: string | null | undefined) {
  if (!value) {
    return
  }
  try {
    await navigator.clipboard.writeText(value)
    ElMessage.success(t('ai.common.copied'))
  } catch {
    ElMessage.warning(value)
  }
}

async function copyRequestId(turn: ChatTurn) {
  await copyValue(turn.requestId)
}

// 进入智能体模式就把「还没处理的写操作」找回来，刷新页面也不丢。
watch(mode, (value) => {
  if (value === 'agent') {
    void loadPendingProposals()
  }
})

onMounted(() => {
  void loadModels()
})

onUnmounted(() => {
  controller?.abort()
  cancelFlush()
  stopCooldown()
  stopProposalTimer()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <ElCard shadow="never" v-loading="modelsLoading">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-col gap-1">
            <span>{{ isAgentMode ? t('ai.chat.modes.agent') : t('ai.chat.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--yg-text-tertiary)">
              {{ isAgentMode ? t('ai.agent.subtitle') : t('ai.chat.subtitle') }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-3">
            <ElSegmented
              v-model="mode"
              class="ai-chat__mode"
              :options="modeOptions"
              :disabled="sending"
            />
            <div v-if="!isAgentMode" class="flex items-center gap-2">
              <span class="yg-text-xs yg-text-tertiary">{{ t('ai.chat.stream') }}</span>
              <ElSwitch v-model="streamEnabled" />
              <span class="text-xs tabular-nums" style="color: var(--yg-text-tertiary)">
                {{ streamEnabled ? t('ai.chat.streamOn') : t('ai.chat.streamOff') }}
              </span>
            </div>
            <ElButton :disabled="turns.length === 0" @click="clearSession">
              <ElIcon class="mr-1"><Delete /></ElIcon>
              {{ t('ai.chat.clear') }}
            </ElButton>
          </div>
        </div>
      </template>

      <div class="flex flex-wrap items-center gap-3">
        <span class="yg-text-xs yg-text-tertiary">{{ t('ai.chat.model') }}</span>
        <ElSelect
          v-model="model"
          class="w-64"
          :placeholder="t('ai.chat.modelPlaceholder')"
          :disabled="!hasModels"
        >
          <ElOption v-for="item in models" :key="item.id" :label="item.id" :value="item.id" />
        </ElSelect>
        <ElButton text @click="loadModels">{{ t('ai.common.refresh') }}</ElButton>
      </div>
    </ElCard>

    <ElCard shadow="never" class="ai-chat">
      <!-- 待确认提案入口：刷新页面后仍在这里等你处理 -->
      <div v-if="isAgentMode" class="yg-card yg-card--narrow ai-agent__pending">
        <div class="yg-card__header ai-agent__pending-head">
          <div class="ai-agent__pending-title-group">
            <span class="yg-card__title ai-agent__pending-title">{{
              t('ai.agent.pending.title')
            }}</span>
            <span v-if="pendingLoading" class="ai-agent__hint">{{
              t('ai.agent.run.replayLoading')
            }}</span>
            <span v-else-if="pendingFailed" class="ai-agent__hint">{{
              t('ai.agent.pending.loadFailed')
            }}</span>
            <span v-else-if="pendingCount > 0" class="ai-agent__pending-count">{{
              t('ai.agent.pending.count', { count: pendingCount })
            }}</span>
            <span v-else class="ai-agent__hint">{{ t('ai.agent.pending.empty') }}</span>
          </div>
          <div class="yg-card__extra ai-agent__pending-actions">
            <ElButton text size="small" @click="loadPendingProposals">{{
              t('ai.agent.pending.refresh')
            }}</ElButton>
            <ElButton text size="small" @click="pendingOpen = !pendingOpen">{{
              pendingOpen ? t('ai.agent.pending.collapse') : t('ai.agent.pending.entry')
            }}</ElButton>
          </div>
        </div>
        <div v-if="pendingOpen" class="yg-card__body ai-agent__pending-body">
          <p v-if="pendingLoading" class="ai-agent__hint">{{ t('ai.agent.run.replayLoading') }}</p>
          <p v-else-if="pendingFailed" class="ai-agent__hint">
            {{ t('ai.agent.pending.loadFailed') }}
          </p>
          <p v-else-if="pendingProposals.length === 0" class="ai-agent__hint">
            {{ t('ai.agent.pending.empty') }}
          </p>
          <ZAiProposalCard
            v-for="card in pendingProposals"
            :key="card.oid"
            :card="card"
            :now="proposalNow"
            @confirm="confirmCard(card)"
            @reject="rejectCard(card)"
          />
        </div>
      </div>

      <div
        ref="listRef"
        class="ai-chat__list"
        :style="{ maxHeight: `${listHeight}px` }"
        @scroll.passive="handleListScroll"
      >
        <div v-if="turns.length === 0" class="py-10">
          <ElEmpty
            :description="
              modelsFailed
                ? t('ai.chat.modelsLoadFailed')
                : !hasModels
                  ? t('ai.chat.modelsEmpty')
                  : isAgentMode
                    ? t('ai.agent.emptyHint')
                    : t('ai.chat.emptyHint')
            "
            :image-size="90"
          />
        </div>

        <div v-for="turn in turns" :key="turn.id" class="ai-chat__row" :class="turn.role">
          <div class="ai-chat__who yg-text-xs yg-text-tertiary">
            {{ turn.role === 'user' ? t('ai.chat.you') : t('ai.chat.assistant') }}
          </div>
          <div class="ai-chat__bubble" :class="turn.role">
            <!-- 智能体：等待整次 run 返回，不做打字机 -->
            <div
              v-if="turn.mode === 'agent' && turn.status === 'pending'"
              class="ai-agent__running"
              role="status"
              aria-live="polite"
            >
              <span class="ai-agent__pulse" aria-hidden="true" />
              <span>{{ t('ai.agent.running') }}</span>
              <span class="ai-agent__hint">{{ t('ai.agent.runningHint') }}</span>
            </div>

            <!-- 智能体：触顶/超时是提示不是错误 -->
            <div
              v-if="turn.mode === 'agent' && turn.stopReason && turn.status === 'done'"
              class="yg-alert yg-alert--warning ai-agent__stop"
              role="status"
            >
              <ElIcon class="yg-alert__icon"><WarningFilled /></ElIcon>
              <span class="yg-alert__title">{{ t(stopReasonKey(turn.stopReason)) }}</span>
              <span class="yg-alert__desc">{{ t(stopReasonHintKey(turn.stopReason)) }}</span>
            </div>

            <!-- 智能体：工具调用时间线（一条调用一行，点开看结果） -->
            <div v-if="turn.events && turn.events.length > 0" class="ai-agent__timeline">
              <div class="ai-agent__timeline-head">
                <span>{{ t('ai.agent.events') }}</span>
                <span>{{ t('ai.agent.eventCount', { count: turn.events.length }) }}</span>
              </div>
              <ul class="ai-agent__events">
                <li v-for="(event, index) in turn.events" :key="index" class="ai-agent__event">
                  <button
                    type="button"
                    class="ai-agent__event-row"
                    :aria-expanded="event.expanded"
                    @click="toggleEvent(event)"
                  >
                    <span
                      class="ai-agent__dot"
                      :class="event.tool_kind === 'write' ? 'is-write' : 'is-read'"
                      aria-hidden="true"
                    />
                    <span class="ai-agent__tool">{{ t(toolLabelKey(event.tool_name)) }}</span>
                    <!-- 写操作不只靠色点区分：文字标签保证色觉障碍下也能读出来 -->
                    <span v-if="event.tool_kind === 'write'" class="ai-agent__kind">
                      {{ t('ai.agent.kindWrite') }}
                    </span>
                    <code v-if="event.args_summary" class="ai-agent__args">
                      {{ event.args_summary }}
                    </code>
                    <span class="ai-agent__badge" :class="eventStatusTone(event.status)">
                      {{ t(`ai.agent.status.${event.status}`) }}
                    </span>
                  </button>
                  <div v-if="event.expanded" class="ai-agent__event-result">
                    <span class="ai-agent__hint">{{ t('ai.agent.result') }}</span>
                    <code class="ai-agent__result-text">{{ event.result_summary }}</code>
                  </div>
                </li>
              </ul>
            </div>

            <!-- 智能体：写操作提案卡（确认才执行） -->
            <ZAiProposalCard
              v-for="card in turn.proposals"
              :key="card.oid"
              :card="card"
              :now="proposalNow"
              @confirm="confirmCard(card)"
              @reject="rejectCard(card)"
            />

            <p v-if="turn.content" class="ai-chat__text">{{ turn.content }}</p>
            <p
              v-else-if="turn.status === 'pending' && turn.mode !== 'agent'"
              class="text-xs"
              style="color: var(--yg-text-tertiary)"
            >
              {{ t('ai.chat.thinking') }}
            </p>

            <div v-if="turn.status === 'error'" class="ai-chat__error">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-medium">{{ turn.errorTitle }}</span>
                <ElTag v-if="turn.errorCode" type="danger" size="small">
                  {{ turn.errorCode }}
                </ElTag>
              </div>
              <p class="text-xs">{{ turn.errorHint }}</p>
              <p
                v-if="turn.quotaUsed !== null && turn.quotaLimit !== null"
                class="text-xs tabular-nums"
              >
                {{
                  t('ai.chat.quotaUsed', {
                    used: turn.quotaUsed,
                    limit: turn.quotaLimit
                  })
                }}
              </p>
              <div v-if="turn.requestId" class="flex flex-wrap items-center gap-2 text-xs">
                <span style="color: var(--yg-text-tertiary)">{{ t('ai.chat.requestId') }}</span>
                <code class="tabular-nums">{{ turn.requestId }}</code>
                <ElButton text size="small" @click="copyRequestId(turn)">
                  <ElIcon class="mr-1"><CopyDocument /></ElIcon>
                  {{ t('ai.chat.copyRequestId') }}
                </ElButton>
              </div>
            </div>

            <p v-else-if="turn.status === 'interrupted'" class="ai-chat__notice">
              {{ t('ai.chat.interrupted') }}
              <span class="text-xs">{{ t('ai.chat.interruptedHint') }}</span>
            </p>
            <p v-else-if="turn.status === 'stopped'" class="ai-chat__notice">
              {{ t('ai.chat.stopped') }}
            </p>

            <!-- 智能体：轮数、用量与「回看这次运行」 -->
            <div v-if="turn.mode === 'agent' && turn.status === 'done'" class="ai-agent__meta">
              <span v-if="turn.rounds">{{
                t('ai.agent.run.rounds', { rounds: turn.rounds })
              }}</span>
              <span v-if="turn.usage && turn.usage.total_tokens !== null">{{
                t('ai.agent.run.usage', { total: turn.usage.total_tokens })
              }}</span>
              <ElButton
                v-if="turn.requestId"
                text
                size="small"
                :loading="turn.replayLoading"
                @click="toggleReplay(turn)"
              >
                {{ turn.replay ? t('ai.agent.run.replayClose') : t('ai.agent.run.replay') }}
              </ElButton>
            </div>
            <p v-if="turn.replayFailed" class="ai-agent__hint">
              {{ t('ai.agent.run.replayFailed') }}
            </p>
            <div v-if="turn.replay" class="ai-agent__replay">
              <p v-if="turn.replay.length === 0" class="ai-agent__hint">
                {{ t('ai.agent.run.replayEmpty') }}
              </p>
              <ul v-else class="ai-agent__replay-list">
                <li v-for="(event, index) in turn.replay" :key="index">
                  <span class="ai-agent__replay-round tabular-nums">{{ event.round_no }}</span>
                  <span>{{ t(toolLabelKey(event.tool_name)) }}</span>
                  <span class="ai-agent__badge" :class="eventStatusTone(event.status)">{{
                    t(`ai.agent.status.${event.status}`)
                  }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>

      <div class="ai-chat__composer">
        <ElInput
          v-model="input"
          type="textarea"
          :rows="3"
          resize="none"
          :placeholder="t('ai.chat.inputPlaceholder')"
          :disabled="!hasModels"
          @keydown.enter.exact.prevent="send"
        />
        <div class="flex flex-wrap items-center justify-between gap-2 mt-2">
          <span class="text-xs" style="color: var(--yg-text-tertiary)">
            <template v-if="cooldown > 0">
              {{ t('ai.chat.rateLimited', { seconds: cooldown }) }}
            </template>
            <template v-else-if="!hasModels">
              {{ modelsFailed ? t('ai.chat.modelsLoadFailed') : t('ai.chat.modelsEmpty') }}
            </template>
          </span>
          <div class="flex items-center gap-2">
            <ElButton v-if="sending" type="danger" plain @click="stop">
              <ElIcon class="mr-1"><VideoPause /></ElIcon>
              {{ t('ai.chat.stop') }}
            </ElButton>
            <ElButton
              type="primary"
              :disabled="!canSend || input.trim().length === 0"
              :loading="sending"
              @click="send"
            >
              <ElIcon class="mr-1"><Promotion /></ElIcon>
              {{ t('ai.chat.send') }}
            </ElButton>
          </div>
        </div>
      </div>
    </ElCard>

    <!-- 双人动态码：运维写操作确认时弹出（复用系统页同一个组件） -->
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
.ai-chat__list {
  overflow-y: auto;
  overscroll-behavior: contain;
  border: 1px solid var(--yg-border-default);
  border-radius: 6px;
  padding: 12px;
  background: var(--yg-bg-surface-sunken);
}

.ai-chat__row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 12px;
}

.ai-chat__row.user {
  align-items: flex-end;
}

.ai-chat__who {
  font-size: 11px;
}

.ai-chat__bubble {
  max-width: min(680px, 88%);
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--yg-border-default);
  background: var(--yg-bg-surface);
}

.ai-chat__bubble.user {
  background: var(--yg-color-primary);
  border-color: var(--yg-color-primary);
  color: var(--yg-color-on-primary);
}

.ai-chat__text {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
}

.ai-chat__error {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border-left: 2px solid var(--yg-color-danger);
  background: var(--yg-bg-surface);
}

.ai-chat__notice {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 8px;
  border-left: 2px solid var(--yg-border-strong);
  color: var(--yg-text-tertiary);
}

.ai-chat__composer {
  margin-top: 12px;
}

/* ---------------------------------------------------------------------------
   智能体：运行态 + 工具时间线 + 待确认入口 + 回看
   颜色/间距/圆角/动效全部走令牌（准则 2.2 / 2.7）；红色只给失败与写操作警示。
   --------------------------------------------------------------------------- */

.ai-chat__mode {
  flex: none;
}

.ai-agent__running {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  padding: var(--yg-space-1) 0;
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-secondary);
}

.ai-agent__pulse {
  flex: none;
  width: var(--yg-space-2);
  height: var(--yg-space-2);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-color-primary);
  animation: ai-agent-pulse var(--yg-dur-page) var(--yg-ease-in-out) infinite;
}

@keyframes ai-agent-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.8);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}

/* 待确认提案入口：常驻在对话区顶部，刷新页面后也能找回未处理的写操作 */
.ai-agent__pending {
  margin-bottom: var(--yg-space-3);
  border-left: 2px solid var(--yg-color-primary);
}

.ai-agent__pending-head {
  flex-wrap: wrap;
  gap: var(--yg-space-2);
}

.ai-agent__pending-title-group {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--yg-space-2);
  min-width: 0;
}

.ai-agent__pending-title {
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
}

.ai-agent__pending-count {
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--yg-text-warning);
}

.ai-agent__pending-actions {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
}

.ai-agent__pending-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding-top: 0;
}

.ai-agent__stop {
  margin-bottom: var(--yg-space-2);
}

.ai-agent__timeline {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  margin-bottom: var(--yg-space-3);
  padding-bottom: var(--yg-space-2);
  border-bottom: 1px solid var(--yg-border-subtle);
}

.ai-agent__timeline-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-agent__events {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ai-agent__event {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

/* 整行是按钮：键盘可聚焦、触控高度 ≥44px（准则 3.7 / 3.8） */
.ai-agent__event-row {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  width: 100%;
  min-height: 44px;
  padding: var(--yg-space-1) var(--yg-space-2);
  border: 0;
  border-radius: var(--yg-radius-sm);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--yg-dur-fast) var(--yg-ease-out);
}

.ai-agent__event-row:hover {
  background: var(--yg-bg-hover);
}

.ai-agent__event-row:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
}

.ai-agent__dot {
  flex: none;
  width: var(--yg-space-2);
  height: var(--yg-space-2);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-text-tertiary);
}

/* 写工具用警示色点：这一行不是查询，而是会产生提案的写操作。 */
.ai-agent__dot.is-write {
  background: var(--yg-color-warning);
}

.ai-agent__tool {
  flex: none;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
  color: var(--yg-text-primary);
}

/* 写操作的文字标签：与警示色点成对出现，不让颜色成为唯一信息载体 */
.ai-agent__kind {
  flex: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-warning);
}

.ai-agent__args {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-agent__badge {
  flex: none;
  padding: 0 var(--yg-space-1);
  border-radius: var(--yg-radius-xs);
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-xs);
  line-height: 1.7;
}

.ai-agent__badge.is-success {
  background: var(--yg-color-success-soft);
  color: var(--yg-text-success);
}

.ai-agent__badge.is-warning {
  background: var(--yg-color-warning-soft);
  color: var(--yg-text-warning);
}

.ai-agent__badge.is-danger {
  background: var(--yg-color-danger-soft);
  color: var(--yg-text-danger);
}

.ai-agent__badge.is-info {
  background: var(--yg-color-info-soft);
  color: var(--yg-text-link);
}

.ai-agent__event-result {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.ai-agent__result-text {
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-xs);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--yg-text-secondary);
}

.ai-agent__hint {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.ai-agent__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-3);
  margin-top: var(--yg-space-2);
  font-size: var(--yg-font-size-xs);
  font-variant-numeric: tabular-nums;
  color: var(--yg-text-tertiary);
}

.ai-agent__replay {
  margin-top: var(--yg-space-2);
  padding: var(--yg-space-2);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
}

.ai-agent__replay-list {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-secondary);
}

.ai-agent__replay-list li {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  min-width: 0;
}

.ai-agent__replay-round {
  flex: none;
  min-width: var(--yg-space-6);
  color: var(--yg-text-tertiary);
}

/* 窄屏：待确认入口的操作按钮换行，不撑破页面 */
@media (max-width: 640px) {
  .ai-agent__pending-actions {
    width: 100%;
  }

  .ai-agent__replay-round {
    min-width: var(--yg-space-5);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ai-agent__pulse {
    animation: none;
    opacity: 1;
  }

  .ai-agent__event-row {
    transition: none;
  }
}
</style>
