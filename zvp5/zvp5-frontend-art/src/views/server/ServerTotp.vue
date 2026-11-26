<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { Refresh, View, Hide } from '@element-plus/icons-vue'
import { isAxiosError } from 'axios'
import { getMyTotp } from '@/api/system/totp.ts'
import { errorDetailOf, resolveErrorMessage } from '@/utils/errorMessage'
import ZHexUnit from './components/ZHexUnit.vue'

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

// 倒计时用连续剩余毫秒归一(而非取整后的整数秒),配合 200ms 定时器才真的平滑递减;
// 配色与「剩余 N 秒」文本仍用整数秒(见 countdownTone / remainsText)
const remainingPct = computed(() => {
  const ms = remainingMs.value
  if (ms === null || ms <= 0) return 0
  return Math.min(100, (ms / (OTP_STEP_SECONDS * 1000)) * 100)
})

/** 徽章语气跟着倒计时走:剩余越少越刺眼,过期直接转危险色。 */
const countdownTone = computed<'accent' | 'warn' | 'danger' | 'idle'>(() => {
  if (loading.value) return 'idle'
  if (!otp.value) return 'idle'
  if (expired.value) return 'danger'
  if (remaining.value <= 10) return 'danger'
  if (remaining.value <= 24) return 'warn'
  return 'accent'
})

const countdownValue = computed(() => {
  if (loading.value) return '···'
  if (!otp.value) return '—'
  if (expired.value) return 'EXP'
  return String(remaining.value)
})

/** 徽章说明只讲状态,秒数由徽章读数承担,避免同一个数字出现两次。 */
const countdownHint = computed(() => {
  if (loading.value) return t('server.hud.loading')
  if (!otp.value) return t(M + 'loadFailed')
  if (expired.value) return t(M + 'unitExpired')
  return t(M + 'unitValid')
})

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
  // 200ms 粒度驱动 now,六边形里的倒计时扇形才平滑收缩;仅作本地倒计时,不发起请求、不写审计日志
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
      if (errorDetailOf(err) === 'USER_BANNED') {
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
    loadError.value = resolveErrorMessage(err, M + 'loadFailed')
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
  <div class="phi-srv-page">
    <section v-if="otp" class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-card__title">{{ t(M + 'codeTitle') }}</h2>
        <ElButton text bg :icon="Refresh" :loading="loading" :disabled="loading" @click="refresh">
          {{ t(M + 'refresh') }}
        </ElButton>
      </header>
      <div class="yg-card__body totp-body">
        <!-- 动态码与它的剩余时间并排：六边形本身就是倒计时指示器 -->
        <div class="totp-stage">
          <div class="totp-code">
            <span class="phi-srv-code">{{ displayCode }}</span>
            <ElButton
              text
              bg
              :icon="showCode ? Hide : View"
              :disabled="expired || loading"
              @click="showCode = !showCode"
            >
              {{ showCode ? t(M + 'hide') : t(M + 'show') }}
            </ElButton>
          </div>
          <div class="totp-countdown">
            <ZHexUnit
              :label="t(M + 'unitLabel')"
              :value="countdownValue"
              :tone="countdownTone"
              :progress="remainingPct"
              size="lg"
              :hint="countdownHint"
            />
            <span class="phi-srv-dim">
              {{ expired ? remainsText : t(M + 'refreshInterval') }}
            </span>
          </div>
        </div>
        <span v-if="loadError" class="phi-srv-dim" style="color: var(--yg-text-danger)">
          {{ loadError }}
        </span>
        <ul class="phi-srv-notes">
          <li>{{ t(M + 'noteSelfOnly') }}</li>
          <li>{{ t(M + 'noteUsage') }}</li>
          <li>{{ t(M + 'noteLog') }}</li>
        </ul>
      </div>
    </section>

    <!-- 首次加载 / 读取失败 -->
    <section v-else class="yg-card">
      <header class="yg-card__header">
        <h2 class="yg-card__title">{{ t(M + 'title') }}</h2>
      </header>
      <div class="yg-card__body">
        <template v-if="loadError && !loading">
          <div class="flex flex-wrap items-center gap-3">
            <span class="phi-srv-dim">{{ loadError }}</span>
            <ElButton text bg :icon="Refresh" @click="refresh">
              {{ t('maintenance.recheck') }}
            </ElButton>
          </div>
        </template>
        <!-- 骨架屏占位:读取期间不再是一句干巴巴的「加载中…」 -->
        <div v-else-if="loading" class="phi-srv-skel">
          <div class="phi-srv-skel__line phi-srv-skel__line--tall"></div>
          <div class="phi-srv-skel__line"></div>
          <div class="phi-srv-skel__line phi-srv-skel__line--short"></div>
        </div>
        <span v-else class="phi-srv-dim">{{ t(M + 'loading') }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.totp-body {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

/* 动态码与倒计时单元并排：窄屏时自然折行 */
.totp-stage {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-4) var(--yg-space-6);
}

.totp-code {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-3);
}

.totp-countdown {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-1);
}
</style>
