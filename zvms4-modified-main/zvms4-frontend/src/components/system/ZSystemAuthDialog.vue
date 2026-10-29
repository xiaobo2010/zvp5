<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElDialog, ElInput } from 'element-plus'

const { t } = useI18n()

/**
 * 父级多久不回话就认为这次授权已失联（毫秒）。
 *
 * 提交后 loading 只能由父级的 succeed()/fail() 释放；父级拼错分支、请求悬挂、
 * 组件被销毁都可能让它永远转下去。超时后按取消语义收口，弹层可以再次打开。
 * 传 0 或负数关闭兜底（老调用方不需要感知这个 prop）。
 */
const props = withDefaults(defineProps<{ timeoutMs?: number }>(), { timeoutMs: 30_000 })

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

/** 父级回话的兜底计时器:提交后开始跑,任何一次收口都会清掉。 */
let timeoutTimer: ReturnType<typeof setTimeout> | null = null

function clearTimeoutTimer() {
  if (timeoutTimer !== null) {
    clearTimeout(timeoutTimer)
    timeoutTimer = null
  }
}

/**
 * 恰好结掉当前这一次 open() 的 Promise。
 *
 * resolveFn 先摘下来再调用,所以后续任何 succeed/cancel/超时都不会重复触发
 * 同一轮的后续逻辑（Promise 本身也只认第一次 resolve）。
 */
function settle(code: string | null) {
  clearTimeoutTimer()
  const resolve = resolveFn
  resolveFn = null
  resolve?.(code)
}

/** 关层/卸载时把内部状态放回初始值,保证下一次 open() 是可用的。 */
function resetState() {
  clearTimeoutTimer()
  resolveFn = null
  loading.value = false
  errorMessage.value = ''
  code.value = ''
}

/**
 * 提交后的兜底:父级一直不回话就把 loading 放开并收起弹层。
 *
 * 这里用 resolve(null) 而不是 reject:所有调用方都按 `if (!code) return`
 * 处理取消,reject 会在没有 try/catch 的调用方里变成 unhandled rejection。
 * 弹层收起后用户可以直接重新发起,不会卡在转圈上。
 */
function armTimeout() {
  clearTimeoutTimer()
  const ms = props.timeoutMs
  if (!(ms > 0)) {
    return
  }
  timeoutTimer = setTimeout(() => {
    timeoutTimer = null
    if (!loading.value) {
      return
    }
    loading.value = false
    settle(null)
    visible.value = false
  }, ms)
}

function open(target: string): Promise<string | null> {
  // 上一次调用还没被回话就又 open():先按取消语义结掉,别留悬空 Promise
  settle(null)
  resetState()
  scene.value = target
  visible.value = true
  return new Promise((resolve) => {
    resolveFn = resolve
  })
}

function succeed() {
  clearTimeoutTimer()
  const resolve = resolveFn
  resolveFn = null
  loading.value = false
  resolve?.(code.value)
  visible.value = false
}

function fail(message: string) {
  // 父级回话了:兜底计时器该停了,否则 30s 后会把这条错误顶掉
  clearTimeoutTimer()
  loading.value = false
  errorMessage.value = message || t('server.auth.failed')
  // 校验失败保留弹层：把原值选中，用户直接敲新码覆盖
  void nextTick(() => inputRef.value?.select())
}

function cancel() {
  settle(null)
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
  if (!resolveFn) {
    // 没有任何父级在等这次输入（上一轮已经 fail/超时/取消收口过）:
    // 再进 loading 只会假死,直接收起弹层,让用户重新发起操作。
    visible.value = false
    return
  }
  errorMessage.value = ''
  loading.value = true
  // resolve 交给父级发起请求;请求失败它会回 fail(),成功回 succeed()
  settle(value)
  armTimeout()
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

// 弹层关掉就把内部状态复位（取消/成功/超时都会走到这里）
watch(visible, (value) => {
  if (!value) {
    resetState()
  }
})

onBeforeUnmount(() => {
  // 组件没了,父级的 await 不能悬着:按取消语义结掉
  settle(null)
  resetState()
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
