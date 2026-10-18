<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElDialog, ElInput } from 'element-plus'

const { t } = useI18n()

const visible = ref(false)
const scene = ref('')
const code = ref('')
const loading = ref(false)
const errorMessage = ref('')

/**
 * Promise 式复用接口(DB 内容页与后续页面同款调用):
 *
 *   const code = await authRef.value?.open('删除 ' + path)
 *   if (code === null) return
 *   try {
 *     await someApi(path, code)
 *     authRef.value?.succeed()
 *   } catch (e) {
 *     authRef.value?.fail(errorMessageFrom(e))
 *   }
 *
 * open() 在用户提交有效 6 位动态码时 resolve;点取消/关闭遮罩 resolve(null)。
 * resolve 后弹层并不关闭:父级通过 succeed() 正常关闭,或 fail(原因) 让弹层
 * 保持打开并显示错误(动态码不对时用户往往直接补一个新码再提交)。
 */
let resolveFn: ((code: string | null) => void) | null = null

function open(target: string): Promise<string | null> {
  scene.value = target
  code.value = ''
  loading.value = false
  errorMessage.value = ''
  visible.value = true
  return new Promise((resolve) => {
    resolveFn = resolve
  })
}

function succeed() {
  resolveFn?.(code.value)
  resolveFn = null
  visible.value = false
}

function fail(message: string) {
  loading.value = false
  errorMessage.value = message || t('server.auth.failed')
}

function cancel() {
  resolveFn?.(null)
  resolveFn = null
  visible.value = false
}

function submit() {
  if (loading.value) return
  const value = code.value.trim()
  if (!/^\d{6}$/.test(value)) {
    errorMessage.value = t('server.auth.formatInvalid')
    return
  }
  errorMessage.value = ''
  loading.value = true
  // resolve 交给父级发起请求;请求失败它会回 fail(),成功回 succeed()
  resolveFn?.(value)
  resolveFn = null
}

defineExpose({ open, succeed, fail })
</script>

<template>
  <ElDialog
    v-model="visible"
    :title="t('server.auth.title')"
    width="380px"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="(v: boolean) => !v && cancel()"
  >
    <div class="space-y-3">
      <div>
        <div class="z-label mb-1">{{ t('server.auth.scene') }}</div>
        <div class="break-all text-[13px] leading-5 text-[var(--zv-text)]">{{ scene }}</div>
      </div>
      <div>
        <div class="z-label mb-1">{{ t('server.auth.codeLabel') }}</div>
        <ElInput
          v-model="code"
          :placeholder="t('server.auth.codePlaceholder')"
          maxlength="6"
          :disabled="loading"
          class="auth-input"
          @keyup.enter="submit"
        />
      </div>
      <p v-if="errorMessage" class="text-[12px] leading-5 text-[#b42318] dark:text-[#ff8a7a]">
        {{ errorMessage }}
      </p>
      <p class="text-[12px] leading-5 text-[var(--zv-label)]">{{ t('server.auth.note') }}</p>
    </div>
    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 py-1.5 text-[13px] text-[var(--zv-text)] transition-colors hover:bg-[var(--zv-surface-alt)]"
          :disabled="loading"
          @click="cancel"
        >
          {{ t('server.auth.cancel') }}
        </button>
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-text)] px-3 py-1.5 text-[13px] text-[var(--zv-surface)] transition-colors hover:opacity-80 disabled:opacity-50"
          :disabled="loading"
          @click="submit"
        >
          {{ loading ? t('server.auth.verifying') : t('server.auth.confirm') }}
        </button>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.auth-input :deep(input) {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3em;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;
}
</style>
