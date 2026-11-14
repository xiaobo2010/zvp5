<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  cancelRun,
  createConversation,
  deleteConversation,
  fetchRunDetail,
  listConversations,
  listMessages,
  submitRun
} from '@/api/next/ai/conversations'
import type { AiConversation, AiMessage, AiRun } from '@/api/next/ai-types'
import { resolveErrorMessageOr } from '@/utils/errorMessage'

/**
 * AI 持久会话面（新后端 /ai/conversations + /ai/runs）。
 *
 * 与 ZAiChat 的流式问答不同：这里每条消息都落库，run 在后台跑，
 * 页面只轮询 run 的增量消息，断线重进也能接上。
 */

const conversations = ref<AiConversation[]>([])
const cursor = ref<string | null>(null)
const loadingList = ref(false)
const listError = ref('')

const activeOid = ref('')
const messages = ref<AiMessage[]>([])
const loadingMessages = ref(false)
const sending = ref(false)
const draft = ref('')
const run = ref<AiRun | null>(null)
const runError = ref('')

let poller: ReturnType<typeof setInterval> | undefined

const active = computed(() => conversations.value.find((item) => item.oid === activeOid.value))
const running = computed(() => {
  const status = run.value?.status
  return status === 'queued' || status === 'running'
})

function stopPolling() {
  if (poller) {
    clearInterval(poller)
    poller = undefined
  }
}

onBeforeUnmount(stopPolling)

async function loadList(reset = true) {
  loadingList.value = true
  listError.value = ''
  try {
    const page = await listConversations({
      limit: 50,
      cursor: reset ? null : cursor.value
    })
    conversations.value = reset
      ? page.conversations
      : [...conversations.value, ...page.conversations]
    cursor.value = page.next_cursor
    if (!activeOid.value && conversations.value.length) {
      await open(conversations.value[0]!.oid)
    }
  } catch (error) {
    listError.value = resolveErrorMessageOr(error, '会话列表加载失败')
  } finally {
    loadingList.value = false
  }
}

async function open(oid: string) {
  activeOid.value = oid
  run.value = null
  runError.value = ''
  messages.value = []
  loadingMessages.value = true
  stopPolling()
  try {
    const page = await listMessages(oid, { limit: 100 })
    messages.value = page.messages
  } catch (error) {
    runError.value = resolveErrorMessageOr(error, '消息加载失败')
  } finally {
    loadingMessages.value = false
  }
}

async function createNew() {
  try {
    const created = await createConversation('chat', '新对话')
    conversations.value = [created, ...conversations.value]
    await open(created.oid)
  } catch (error) {
    ElMessage.error(resolveErrorMessageOr(error, '新建会话失败'))
  }
}

async function remove(conversation: AiConversation) {
  try {
    await ElMessageBox.confirm(`删除《${conversation.title || '未命名'}》？`, '删除会话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }
  try {
    await deleteConversation(conversation.oid)
    conversations.value = conversations.value.filter((item) => item.oid !== conversation.oid)
    if (activeOid.value === conversation.oid) {
      messages.value = []
      activeOid.value = ''
    }
  } catch (error) {
    ElMessage.error(resolveErrorMessageOr(error, '删除失败'))
  }
}

async function poll(runId: string, sinceSeq: number) {
  try {
    const detail = await fetchRunDetail(runId, 0, sinceSeq)
    run.value = detail.run
    const incoming = detail.messages ?? []
    if (incoming.length) {
      messages.value = [...messages.value, ...incoming]
    }
    if (detail.run.status === 'failed') {
      runError.value = detail.run.error_message || '本次执行失败'
      stopPolling()
      sending.value = false
    }
    if (['succeeded', 'cancelled', 'failed'].includes(detail.run.status)) {
      stopPolling()
      sending.value = false
    }
  } catch (error) {
    runError.value = resolveErrorMessageOr(error, '执行状态拉取失败')
    stopPolling()
    sending.value = false
  }
}

async function send() {
  const content = draft.value.trim()
  if (!content || !activeOid.value || sending.value) {
    return
  }
  sending.value = true
  runError.value = ''
  try {
    const result = await submitRun(activeOid.value, content, {
      clientMsgId: `web-${Date.now()}`
    })
    draft.value = ''
    const lastSeq = messages.value.length
      ? messages.value[messages.value.length - 1]!.seq
      : 0
    // 立刻把用户这条塞进列表，等 run 的增量消息补 assistant 部分
    messages.value = [
      ...messages.value,
      {
        oid: result.message_oid,
        seq: lastSeq + 1,
        role: 'user',
        content,
        created_at: new Date().toISOString()
      } as AiMessage
    ]
    stopPolling()
    poller = setInterval(() => void poll(result.run_id, lastSeq + 1), 1500)
    void poll(result.run_id, lastSeq + 1)
  } catch (error) {
    runError.value = resolveErrorMessageOr(error, '发送失败')
    sending.value = false
  }
}

async function cancel() {
  if (!run.value) {
    return
  }
  try {
    await cancelRun(run.value.oid)
    ElMessage.success('已请求中止')
  } catch (error) {
    ElMessage.error(resolveErrorMessageOr(error, '取消失败'))
  }
}

onMounted(() => void loadList())
</script>

<template>
  <div class="sx">
    <aside class="sx-side">
      <div class="sx-side__head">
        <span class="sx-side__title">会话</span>
        <button class="yg2-btn yg2-btn--sm yg2-btn--primary" type="button" @click="createNew">
          新建
        </button>
      </div>

      <p v-if="listError" class="sx-hint sx-hint--error">{{ listError }}</p>
      <p v-else-if="loadingList && !conversations.length" class="sx-hint">加载中…</p>
      <p v-else-if="!conversations.length" class="sx-hint">还没有会话，点「新建」开一个。</p>

      <ul class="sx-list">
        <li v-for="item in conversations" :key="item.oid">
          <button
            class="sx-item"
            :class="{ 'is-active': item.oid === activeOid }"
            type="button"
            @click="open(item.oid)"
          >
            <span class="sx-item__title">{{ item.title || '未命名会话' }}</span>
            <span class="sx-item__meta">{{ item.message_count }} 条 · {{ item.mode }}</span>
          </button>
          <button class="sx-item__del" type="button" title="删除" @click="remove(item)">×</button>
        </li>
      </ul>

      <button
        v-if="cursor"
        class="yg2-btn yg2-btn--quiet yg2-btn--sm"
        type="button"
        @click="loadList(false)"
      >
        加载更多
      </button>
    </aside>

    <section class="sx-main yg2-card">
      <header class="sx-main__head">
        <div>
          <p class="sx-main__title">{{ active?.title || '未选择会话' }}</p>
          <p class="sx-main__sub">
            {{ active ? `${active.message_count} 条消息 · ${active.model || '默认模型'}` : '从左侧选一个会话' }}
          </p>
        </div>
        <button
          v-if="running"
          class="yg2-btn yg2-btn--sm yg2-btn--danger"
          type="button"
          @click="cancel"
        >
          中止执行
        </button>
      </header>

      <div class="sx-thread">
        <p v-if="loadingMessages" class="sx-hint">消息加载中…</p>
        <p v-else-if="!messages.length" class="sx-hint">
          还没有消息。问点什么吧 —— 回答会在后台执行，关掉页面也不会丢。
        </p>
        <article
          v-for="item in messages"
          :key="item.oid"
          class="sx-msg"
          :class="`sx-msg--${item.role}`"
        >
          <p class="sx-msg__role">{{ item.role === 'user' ? '我' : item.role === 'tool' ? '工具' : '助手' }}</p>
          <p class="sx-msg__body">{{ item.content }}</p>
        </article>
      </div>

      <p v-if="runError" class="sx-hint sx-hint--error">{{ runError }}</p>

      <form class="sx-compose" @submit.prevent="send">
        <textarea
          v-model="draft"
          class="yg2-input sx-compose__input"
          rows="2"
          placeholder="输入消息，Enter 发送（Shift+Enter 换行）"
          :disabled="!activeOid || sending"
          @keydown.enter.exact.prevent="send"
        />
        <button
          class="yg2-btn yg2-btn--primary"
          type="submit"
          :disabled="!activeOid || sending || !draft.trim()"
        >
          {{ sending ? '执行中…' : '发送' }}
        </button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.sx {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  min-height: 460px;
}

.sx-side {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--yg2-line);
  border-radius: var(--yg2-r-20);
  background: var(--yg2-surface);
}

.sx-side__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sx-side__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--yg2-text-secondary);
}

.sx-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 380px;
  overflow: auto;
}

.sx-list li {
  display: flex;
  align-items: center;
  gap: 2px;
}

.sx-item {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--yg2-r-10, 10px);
  background: transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.sx-item:hover {
  background: var(--yg2-surface-sunken);
}

.sx-item.is-active {
  background: var(--yg2-accent-soft);
}

.sx-item__title {
  font-size: 13px;
  font-weight: 500;
  color: var(--yg2-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sx-item__meta {
  font-size: 11px;
  color: var(--yg2-text-tertiary);
}

.sx-item__del {
  flex: none;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--yg2-text-tertiary);
  cursor: pointer;
}

.sx-item__del:hover {
  background: var(--yg2-act-danger-bg, #fdeaea);
  color: var(--yg2-act-danger-text, #b23a34);
}

.sx-main {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  min-height: 460px;
}

.sx-main__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--yg2-sep);
}

.sx-main__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--yg2-text);
}

.sx-main__sub {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--yg2-text-tertiary);
}

.sx-thread {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: auto;
  padding: 2px;
}

.sx-msg {
  max-width: 78%;
  padding: 10px 12px;
  border-radius: 14px;
  background: var(--yg2-surface-sunken);
}

.sx-msg--user {
  align-self: flex-end;
  background: var(--yg2-accent-soft);
}

.sx-msg__role {
  margin: 0 0 4px;
  font-size: 11px;
  color: var(--yg2-text-tertiary);
}

.sx-msg__body {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--yg2-text);
  white-space: pre-wrap;
  word-break: break-word;
}

.sx-compose {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.sx-compose__input {
  flex: 1 1 auto;
  min-height: 56px;
  resize: vertical;
}

.sx-hint {
  margin: 0;
  padding: 6px 2px;
  font-size: 12.5px;
  color: var(--yg2-text-tertiary);
}

.sx-hint--error {
  color: var(--yg2-act-danger-text, #b23a34);
}

@media (max-width: 1024px) {
  .sx {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
