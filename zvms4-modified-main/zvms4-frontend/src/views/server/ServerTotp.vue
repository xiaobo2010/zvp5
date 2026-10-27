<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElCard, ElButton, ElProgress } from 'element-plus'
import { Refresh, View, Hide } from '@element-plus/icons-vue'
import { isAxiosError } from 'axios'
import { getMyTotp } from '@/api/system/totp.ts'

const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

if (!userStore.position.includes('admin')) {
  router.replace('/not-found')
}

const isAdmin = userStore.position.includes('admin')

const M = 'server.totp.'

// 认证窗口 60 秒,与后端 OTP_STEP 一致(util/cert.py: OTP_STEP = 60)
const OTP_STEP_SECONDS = 60

const otp = ref<string | null>(null)
const expSeconds = ref<number | null>(null)
const showCode = ref(false)
const loading = ref(false)
const loadError = ref('')
const remaining = ref(0)
let timer: ReturnType<typeof setInterval> | undefined
// 每个过期窗口(以 exp 标识)最多自动续期一次;失败后 exp 不变,因此不会重试,
// 避免 60s 一次的错误风暴。服务器时钟偏差导致新 exp 仍已过期时同样不会循环。
let autoRefreshedForExp: number | null = null

// 从后端错误里提取 detail 用于展示
function detailOf(err: unknown): string {
  if (isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

// 时间以响应式值推进:timer 更新 now,依赖它的 computed 随之失效重算(computed 内不直接取 Date.now())
const now = ref(Date.now())

// exp 为 Unix 秒(util/cert.py get_current_totp: (counter + 1) * OTP_STEP)
const remainingMs = computed(() => {
  if (expSeconds.value === null) return null
  return expSeconds.value * 1000 - now.value
})

const expired = computed(() => {
  const ms = remainingMs.value
  return ms !== null && ms <= 0
})

// 进度条用连续剩余毫秒归一(而非取整后的整数秒),配合 200ms 定时器才真的平滑递减;
// 配色与「剩余 N 秒」文本仍用整数秒(见 countdownColor / remainsText)
const remainingPct = computed(() => {
  const ms = remainingMs.value
  if (ms === null || ms <= 0) return 0
  return Math.min(100, (ms / (OTP_STEP_SECONDS * 1000)) * 100)
})

// 临近过期转暖色:≤10 秒红,≤24 秒橙(约 17%/40% 窗口,与旧 30 秒窗口阈值等比换算)
function countdownColor(seconds: number): string {
  if (seconds <= 10) return '#f56c6c'
  if (seconds <= 24) return '#e6a23c'
  return '#409eff'
}

const displayCode = computed(() => {
  return showCode.value && otp.value && !expired.value ? otp.value : '••••••'
})

const remainsText = computed(() => {
  if (expSeconds.value === null) return ''
  if (remaining.value <= 0) return t(M + 'expired')
  return t(M + 'expiresIn', { seconds: remaining.value })
})

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = undefined
  }
}

function tick() {
  now.value = Date.now()
  const ms = remainingMs.value
  if (ms === null) return
  remaining.value = ms > 0 ? Math.ceil(ms / 1000) : 0
  if (remaining.value <= 0) {
    // 过期后收起明文，但保留卡片与刷新入口；清空 otp 会让页面误落到“加载中“。
    showCode.value = false
    stopTimer()
    autoRefreshOnce()
  }
}

// 过期自动续期:仅在页面可见、无请求在飞、且该 exp 窗口还没自动刷过时触发。
// 后台标签页不发起请求；失败由 refresh 内部落 loadError,这里不重试。
function autoRefreshOnce() {
  if (loading.value) return
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  const exp = expSeconds.value
  if (exp === null || autoRefreshedForExp === exp) return
  autoRefreshedForExp = exp
  void refresh()
}

function startTimer() {
  stopTimer()
  tick()
  // 200ms 粒度驱动 now,进度条平滑递减;仅作本地倒计时,不发起请求、不写审计日志
  timer = setInterval(tick, 200)
}

async function refresh() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await getMyTotp(String(userStore._id))
    otp.value = res.data.data.otp
    expSeconds.value = res.data.data.exp
    // 服务器时钟偏差导致返回的窗口本身已过期:标记为「已自动刷过」,
    // 避免 tick 每次命中过期再发请求形成循环。
    if (expSeconds.value * 1000 <= Date.now()) autoRefreshedForExp = expSeconds.value
    showCode.value = false
    startTimer()
  } catch (err) {
    // 403:封禁时后端返回 403 + detail='USER_BANNED'(机器可读协议,不直接展示),
    // 由全局遮罩接管,页面级不跳转、不落错误态;其余 403 = 非本人,兜底跳 404 页
    if (isAxiosError(err) && err.response?.status === 403) {
      if (detailOf(err) === 'USER_BANNED') {
        stopTimer()
      } else {
        router.replace('/not-found')
      }
      return
    }
    if (isAxiosError(err) && err.response?.status === 404) {
      loadError.value = t(M + 'notFound')
      stopTimer()
      return
    }
    loadError.value = detailOf(err) || t(M + 'loadFailed')
    stopTimer()
  } finally {
    loading.value = false
  }
}

if (isAdmin) {
  onMounted(() => {
    void refresh()
  })
}

onUnmounted(() => {
  stopTimer()
})
</script>

<template>
  <!-- 登录动态码:仅 admin 可见(域守卫),接口本身也只允许本人 -->
  <ElCard v-if="otp" shadow="never">
    <template #header>
      <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span class="font-medium">{{ t(M + 'title') }}</span>
        <ElButton text bg :icon="Refresh" :loading="loading" @click="refresh">
          {{ t(M + 'refresh') }}
        </ElButton>
      </div>
    </template>
    <div class="flex flex-col gap-4">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-3">
        <span class="font-mono text-4xl tracking-[0.35em] dark:text-gray-100">
          {{ displayCode }}
        </span>
        <ElButton text bg :icon="showCode ? Hide : View" :disabled="expired" @click="showCode = !showCode">
          {{ showCode ? t(M + 'hide') : t(M + 'show') }}
        </ElButton>
        <span
          v-if="expired"
          class="text-sm text-orange-600 dark:text-orange-400"
        >
          {{ t(M + 'expired') }}
        </span>
      </div>
      <div class="flex w-full max-w-sm flex-col gap-1">
        <ElProgress
          :percentage="remainingPct"
          :color="countdownColor(remaining)"
          :show-text="false"
        />
        <span class="text-sm text-gray-500 dark:text-gray-400">
          {{ remainsText }} · {{ t(M + 'refreshInterval') }}
        </span>
        <span v-if="loadError" class="text-sm text-red-600 dark:text-red-400">
          {{ loadError }}
        </span>
      </div>
      <ul class="flex list-disc flex-col gap-1 pl-5 text-sm text-gray-600 dark:text-gray-300">
        <li>{{ t(M + 'noteSelfOnly') }}</li>
        <li>{{ t(M + 'noteUsage') }}</li>
        <li>{{ t(M + 'noteLog') }}</li>
      </ul>
    </div>
  </ElCard>

  <!-- 首次加载 / 读取失败 -->
  <ElCard v-else shadow="never">
    <template #header>
      <span class="font-medium">{{ t(M + 'title') }}</span>
    </template>
    <div class="flex min-h-24 flex-wrap items-center gap-3">
      <template v-if="loadError && !loading">
        <span class="text-sm text-gray-500 dark:text-gray-400">{{ loadError }}</span>
        <ElButton text bg :icon="Refresh" @click="refresh">
          {{ t('maintenance.recheck') }}
        </ElButton>
      </template>
      <span v-else class="text-sm text-gray-500 dark:text-gray-400">{{ t(M + 'loading') }}</span>
    </div>
  </ElCard>
</template>

<style scoped>
/* EP 进度条 theme 写死 `transition: width 0.6s ease`，而倒计时每 200ms tick 一次，
   过渡永远重启、条一直追不上真实剩余时间。本页把时长压到 200ms 与 tick 对齐。 */
:deep(.el-progress-bar__inner) {
  transition-duration: 200ms;
}
</style>
