<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { verifyAdminPassword } from '@/api/admin-verification'
import BubbleFeedback from '@/components/BubbleFeedback.vue'

const dialog = ref<HTMLDialogElement>()
const password = ref('')
const error = ref('')
const busy = ref(false)
let finish: ((token: string | null) => void) | undefined
let generation = 0

function close(token: string | null = null) {
  generation += 1
  password.value = ''
  error.value = ''
  busy.value = false
  dialog.value?.close()
  const resolve = finish
  finish = undefined
  resolve?.(token)
}

function request(): Promise<string | null> {
  const target = dialog.value
  if (finish || !target?.isConnected) return Promise.resolve(null)
  password.value = ''
  error.value = ''
  target.showModal()
  return new Promise((resolve) => {
    finish = resolve
  })
}

async function submit() {
  if (busy.value || !password.value) return
  busy.value = true
  error.value = ''
  const attempt = generation
  const proof = verifyAdminPassword(password.value)
  password.value = ''
  try {
    const token = await proof
    if (attempt === generation) close(token)
  } catch (reason) {
    if (attempt === generation) error.value = reason instanceof Error ? reason.message : '密码验证失败，请重试。'
  } finally {
    if (attempt === generation) busy.value = false
  }
}

onBeforeUnmount(() => close())
defineExpose({ request })
</script>

<template>
  <dialog ref="dialog" class="write-verification" aria-labelledby="admin-write-verification-title" @cancel.prevent="close()">
    <form @submit.prevent="submit">
      <h2 id="admin-write-verification-title">确认管理员密码</h2>
      <p>继续此操作前，请输入当前账号的密码。</p>
      <label for="admin-write-password">当前密码</label>
      <input id="admin-write-password" v-model="password" class="input" type="password" autocomplete="current-password" autofocus required :disabled="busy" />
      <BubbleFeedback v-if="error" class="glass-feedback--error" role="alert">{{ error }}</BubbleFeedback>
      <div class="write-verification__actions">
        <button type="button" @click="close()">取消</button>
        <button type="submit" :disabled="busy || !password">{{ busy ? '验证中…' : '验证并继续' }}</button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
.write-verification {
  width: min(420px, calc(100% - 32px));
  margin: auto;
  padding: var(--space-5);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background: var(--surface-matte);
  color: var(--color-text-primary);
  box-shadow: var(--glass-floating-shadow);
}
.write-verification::backdrop { background: rgb(16 27 50 / 42%); }
.write-verification form { display: grid; gap: var(--space-3); }
.write-verification h2 { font-size: var(--font-size-headline); }
.write-verification p, .write-verification label { font-size: var(--font-size-callout); }
.write-verification input, .write-verification button {
  min-height: 44px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-sm);
  background: white;
  color: inherit;
  font: inherit;
}
.write-verification__actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); }
.write-verification button[type='submit'] { background: var(--accent-ink); color: white; }
.write-verification button:disabled { opacity: 0.5; }
</style>
