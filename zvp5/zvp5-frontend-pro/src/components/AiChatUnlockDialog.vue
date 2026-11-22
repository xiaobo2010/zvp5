<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { unlockAgent } from '@/api/ai-chat'

/**
 * Chat-specific password prompt for the server-side agent unlock window.
 *
 * A persisted send (`POST /ai/conversations/{oid}/runs`) is rejected with
 * `AI_AGENT_UNLOCK_REQUIRED` until the account password has unlocked the user
 * for 30 minutes. This dialog only proves the password; it never stores it and
 * never decides permissions — the server owns the window.
 */

const dialog = ref<HTMLDialogElement>()
const password = ref('')
const error = ref('')
const busy = ref(false)

let finish: ((unlocked: boolean) => void) | undefined
let generation = 0

function close(unlocked = false) {
  generation += 1
  password.value = ''
  error.value = ''
  busy.value = false
  dialog.value?.close()
  const resolve = finish
  finish = undefined
  resolve?.(unlocked)
}

function request(): Promise<boolean> {
  const target = dialog.value
  if (finish || !target?.isConnected) return Promise.resolve(false)
  password.value = ''
  error.value = ''
  target.showModal()
  return new Promise((resolve) => {
    finish = resolve
  })
}

/** Close a prompt that belongs to a view the panel has just left. */
function cancel(): void {
  if (finish) close(false)
}

function errorText(reason: unknown): string {
  if (reason instanceof TypeError) return '网络连接失败，请稍后重试。'
  if (!reason || typeof reason !== 'object') return '验证失败，请重试。'
  const record = reason as Record<string, unknown>
  const code = typeof record.code === 'string' ? record.code : ''
  const status = typeof record.status === 'number' ? record.status : 0
  if (code === 'AI_AGENT_UNLOCK_FAILED') return '密码不正确，请重试。'
  if (code === 'AI_AGENT_UNLOCK_LOCKED') return '连续验证失败次数过多，请 10 分钟后再试。'
  if (code === 'AI_FORBIDDEN') return '当前账号没有智能体权限。'
  if (code === 'AI_UNAUTHORIZED' || status === 401) return '登录状态已失效，请重新登录。'
  const message = typeof record.message === 'string' ? record.message : ''
  if (message) return message
  return status ? `验证失败（${status}），请重试。` : '验证失败，请重试。'
}

async function submit() {
  if (busy.value || !password.value) return
  busy.value = true
  error.value = ''
  const attempt = generation
  try {
    await unlockAgent(password.value)
    if (attempt === generation) close(true)
  } catch (reason) {
    if (attempt === generation) error.value = errorText(reason)
  } finally {
    if (attempt === generation) busy.value = false
  }
}

onBeforeUnmount(() => close(false))

defineExpose({ request, cancel })
</script>

<template>
  <dialog
    ref="dialog"
    class="chat-unlock"
    aria-labelledby="chat-unlock-title"
    @cancel.prevent="close(false)"
  >
    <form @submit.prevent="submit">
      <h2 id="chat-unlock-title">解锁智能体对话</h2>
      <p>发送消息前需要验证当前账号密码，验证通过后 30 分钟内有效。</p>
      <label for="chat-unlock-password">当前密码</label>
      <input
        id="chat-unlock-password"
        v-model="password"
        class="input"
        type="password"
        autocomplete="current-password"
        autofocus
        required
        maxlength="200"
        :disabled="busy"
      />
      <BubbleFeedback v-if="error" class="glass-feedback--error" role="alert">
        {{ error }}
      </BubbleFeedback>
      <div class="chat-unlock__actions">
        <button type="button" :disabled="busy" @click="close(false)">取消</button>
        <button type="submit" :disabled="busy || !password">
          {{ busy ? '验证中…' : '验证并解锁' }}
        </button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
.chat-unlock {
  width: min(420px, calc(100% - 32px));
  margin: auto;
  padding: var(--space-5);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background: var(--surface-matte);
  color: var(--color-text-primary);
  box-shadow: var(--glass-floating-shadow);
}

.chat-unlock::backdrop {
  background: rgb(16 27 50 / 42%);
}

.chat-unlock form {
  display: grid;
  gap: var(--space-3);
}

.chat-unlock h2 {
  font-size: var(--font-size-headline);
}

.chat-unlock p,
.chat-unlock label {
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
  color: var(--color-text-secondary);
}

.chat-unlock input,
.chat-unlock button {
  min-height: 44px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-sm);
  background: #ffffff;
  color: inherit;
  font: inherit;
}

.chat-unlock input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
}

.chat-unlock__actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.chat-unlock button {
  cursor: pointer;
}

.chat-unlock button[type='submit'] {
  background: var(--accent-ink);
  color: #ffffff;
}

.chat-unlock button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
