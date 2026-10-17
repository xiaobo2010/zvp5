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

// 认证窗口 30 秒,与后端 OTP_STEP 一致(util/cert.py: OTP_STEP = 30)
const OTP_STEP_SECONDS = 30

const otp = ref<string | null>(null)
const expSeconds = ref<number | null>(null)
const showCode = ref(false)
const loading = ref(false)
const loadError = ref('')
const remaining = ref(0)
let timer: ReturnType<typeof setInterval> | undefined

// 从后端错误里提取 detail 用于展示
function detailOf(err: unknown): string {
  if (isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

// exp 为 Unix 秒(util/cert.py get_current_totp: (counter + 1) * OTP_STEP)
const remainingMs = computed(() => {
  if (expSeconds.value === null) return null
  return expSeconds.value * 1000 - Date.now()
})

const expired = computed(() => {
  const ms = remainingMs.value
  return ms !== null && ms <= 0
})

// 剩余秒数以 30 秒窗口归一,供进度条与配色使用
const remainingPct = computed(() => {
  if (remaining.value <= 0) return 0
  return Math.min(100, (remaining.value / OTP_STEP_SECONDS) * 100)
})

// 临近过期转暖色:≤5 秒红,≤12 秒橙
function countdownColor(seconds: number): string {
  if (seconds <= 5) return '#f56c6c'
  if (seconds <= 12) return '#e6a23c'
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
  const ms = remainingMs.value
  if (ms === null) return
  remaining.value = ms > 0 ? Math.ceil(ms / 1000) : 0
  if (remaining.value <= 0) {
    // 过期后收起明文,避免展示已失效的动态码
    otp.value = null
    showCode.value = false
    stopTimer()
  }
}

function startTimer() {
  stopTimer()
  tick()
  timer = setInterval(tick, 1000)
}

async function refresh() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await getMyTotp(String(userStore._id))
    otp.value = res.data.data.otp
    expSeconds.value = res.data.data.exp
    showCode.value = false
    startTimer()
  } catch (err) {
    // 403:登录账号与查询目标不一致;页面守卫之下仅作兜底
    if (isAxiosError(err) && err.response?.status === 403) {
      router.replace('/not-found')
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
  <ElCard v-if="otp" shadow="never" class="mt-8">
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
      </div>
      <ul class="flex list-disc flex-col gap-1 pl-5 text-sm text-gray-600 dark:text-gray-300">
        <li>{{ t(M + 'noteSelfOnly') }}</li>
        <li>{{ t(M + 'noteUsage') }}</li>
        <li>{{ t(M + 'noteLog') }}</li>
      </ul>
    </div>
  </ElCard>

  <!-- 首次加载 / 读取失败 -->
  <ElCard v-else shadow="never" class="mt-8">
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