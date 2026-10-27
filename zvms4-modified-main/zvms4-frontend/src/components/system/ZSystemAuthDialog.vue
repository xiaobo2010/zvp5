<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElDialog, ElInput } from 'element-plus'

const { t } = useI18n()

const visible = ref(false)
const scene = ref('')
const code = ref('')
const loading = ref(false)
const errorMessage = ref('')
const inputRef = ref<InstanceType<typeof ElInput> | null>(null)

/** 桌面 420px，窄屏留 16px 边距。 */
const dialogWidth = 'min(420px, calc(100vw - 32px))'

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
  // 校验失败保留弹层：把原值选中，用户直接敲新码覆盖
  void nextTick(() => inputRef.value?.select())
}

function cancel() {
  resolveFn?.(null)
  resolveFn = null
  visible.value = false
}

/** 只留数字、最多 6 位（粘贴带空格/分隔符也能收干净）。 */
function formatCode(value: string): string {
  return value.replace(/\D/g, '').slice(0, 6)
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

function focusInput() {
  inputRef.value?.focus()
}

// 满 6 位自动提交；回车提交仍然保留
watch(code, (value) => {
  if (value.length === 6) {
    submit()
  }
})

defineExpose({ open, succeed, fail, cancel })
</script>

<template>
  <ElDialog
    v-model="visible"
    :width="dialogWidth"
    :close-on-click-modal="false"
    append-to-body
    @opened="focusInput"
    @update:model-value="(v: boolean) => !v && cancel()"
  >
    <template #header="{ titleId }">
      <h2 :id="titleId" class="auth-head">
        {{ t('server.auth.title') }}
      </h2>
    </template>

    <div class="auth-body">
      <p class="auth-scene">
        {{ t('server.auth.sceneLine', { target: scene }) }}
      </p>

      <div class="auth-field">
        <label class="auth-field__label" for="z-auth-code">{{ t('server.auth.codeLabel') }}</label>
        <ElInput
          id="z-auth-code"
          ref="inputRef"
          v-model="code"
          class="auth-field__input"
          maxlength="6"
          inputmode="numeric"
          autocomplete="one-time-code"
          autofocus
          :formatter="formatCode"
          :parser="formatCode"
          :placeholder="t('server.auth.codePlaceholder')"
          :disabled="loading"
          @keyup.enter="submit"
        />
      </div>

      <!-- 始终占一行高度：报错不顶动下面的说明与按钮 -->
      <p class="auth-error" role="status" aria-live="polite">{{ errorMessage }}</p>
      <p class="auth-note">{{ t('server.auth.note') }}</p>
    </div>

    <template #footer>
      <div class="auth-actions">
        <ElButton class="auth-actions__btn" :disabled="loading" @click="cancel">
          {{ t('server.auth.cancel') }}
        </ElButton>
        <ElButton
          class="auth-actions__btn"
          type="primary"
          :loading="loading"
          :disabled="loading"
          @click="submit"
        >
          {{ loading ? t('server.auth.verifying') : t('server.auth.confirm') }}
        </ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
/* 标题：与弹窗标题栏同一基线，只保留字重与字号 */
.auth-head {
  margin: 0;
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-semibold);
  line-height: var(--yg-line-height-tight);
  color: var(--yg-text-primary);
}

.auth-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-3);
}

/* 目标操作：一行说明 + 具体操作名（路径可能很长，允许断行） */
.auth-scene {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--yg-space-1) var(--yg-space-2);
  margin: 0;
  padding: var(--yg-space-2) var(--yg-space-3);
  border: 1px solid var(--yg-border-subtle);
  border-radius: var(--yg-radius-md);
  background: var(--yg-bg-surface-sunken);
  font-size: var(--yg-font-size-sm);
}

.auth-scene__label {
  flex: none;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

.auth-scene__target {
  min-width: 0;
  font-family: var(--yg-font-mono);
  color: var(--yg-text-primary);
  word-break: break-all;
}

.auth-field {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}

.auth-field__label {
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

/* 动态码：等宽 + 字距 + 居中，一眼能数清 6 位 */
.auth-field__input {
  --el-input-height: var(--yg-control-height-lg);
}

.auth-field__input :deep(.el-input__wrapper) {
  min-height: var(--yg-control-height-lg);
  padding-inline: var(--yg-space-3);
  border-radius: var(--yg-radius-md);
}

.auth-field__input :deep(.el-input__inner) {
  text-align: center;
  font-family: var(--yg-font-mono);
  font-size: var(--yg-font-size-2xl);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.35em;
  text-indent: 0.35em;
}

.auth-field__input :deep(.el-input__wrapper.is-focus) {
  box-shadow: var(--yg-focus-ring);
}

/* 预留一行：错误出现/消失时按钮不跳动 */
.auth-error {
  min-height: calc(var(--yg-font-size-sm) * var(--yg-line-height-normal));
  margin: 0;
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-danger);
}

.auth-note {
  margin: 0;
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
  color: var(--yg-text-tertiary);
}

.auth-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* 窄屏：按钮各占一行，主操作在最后 */
@media (max-width: 480px) {
  .auth-actions {
    flex-direction: column;
  }

  .auth-actions__btn {
    width: 100%;
  }

  .auth-actions__btn + .auth-actions__btn {
    margin-top: var(--yg-space-2);
    margin-left: 0;
  }
}
</style>
