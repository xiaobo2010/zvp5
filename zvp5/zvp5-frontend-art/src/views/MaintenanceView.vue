<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElCard } from 'element-plus/es/components/card/index.mjs'
import { ElResult } from 'element-plus/es/components/result/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import dayjs from '@/plugins/dayjs'
import {
  getMaintenanceStatus,
  type MaintenanceStatus
} from '@/api/system/maintenance.ts'

// 页面状态机:checking 查询中 / maintenance 维护中 / ended 刚恢复 / normal 本就未维护
type Phase = 'checking' | 'maintenance' | 'ended' | 'normal'

// 10 分钟:该接口公开无鉴权,降频防刷
const STATUS_POLL_INTERVAL_MS = 600000

const { t } = useI18n()
const router = useRouter()

const phase = ref<Phase>('checking')
const status = ref<MaintenanceStatus | null>(null)
let timer: number | undefined

function startPolling() {
  // 幂等:避免重复叠加定时器
  if (timer === undefined) {
    timer = window.setInterval(() => void check(), STATUS_POLL_INTERVAL_MS)
  }
}

function stopPolling() {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

const announcement = computed(() => status.value?.announcement ?? '')
const sinceText = computed(() => {
  const since = status.value?.since
  // since 为 unix 秒,dayjs 需毫秒
  if (!since) return ''
  return dayjs(since * 1000).format('YYYY-MM-DD HH:mm:ss')
})

async function check() {
  try {
    const res = await getMaintenanceStatus()
    const next = res.data.data
    const wasMaintaining = status.value?.enabled === true
    status.value = next
    if (next.enabled) {
      phase.value = 'maintenance'
      // 维护中保持 10 分钟轮询
      startPolling()
    } else {
      // 本次会话曾处于维护中则视为「维护已结束」,否则视为「当前未在维护」
      phase.value = wasMaintaining ? 'ended' : 'normal'
      // 已进入终态:停止轮询
      stopPolling()
    }
  } catch {
    // 查询失败静默,等待下一次自动轮询
  }
}

function goHome() {
  router.push('/')
}

onMounted(() => {
  void check()
  // 首查期间保持轮询兜底:若首查成功进入终态(ended/normal),check 内会立即停掉
  startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <div class="p-4 flex items-center justify-center" style="min-height: 80vh">
    <ElCard shadow="never" class="w-full max-w-xl">
      <ElResult
        v-if="phase === 'checking'"
        icon="info"
        :title="t('maintenance.checking')"
      />
      <ElResult
        v-else-if="phase === 'maintenance'"
        icon="warning"
        :title="t('maintenance.title')"
        :sub-title="announcement ? '' : t('maintenance.defaultDescription')"
      >
        <template #extra>
          <div class="flex flex-col items-center gap-3 w-full">
            <ElAlert
              v-if="announcement"
              type="warning"
              show-icon
              :closable="false"
              :title="announcement"
              class="w-full whitespace-pre-line"
            />
            <span v-if="sinceText" class="text-sm text-gray-500 dark:text-gray-400">
              {{ t('maintenance.startedAt') }} {{ sinceText }}
            </span>
            <span class="text-xs text-gray-400 dark:text-gray-500">
              {{ t('maintenance.autoCheckNote') }}
            </span>
            <!-- 低调的管理员入口:始终显示,不携带任何角色/登录态信息 -->
            <ElButton
              text
              size="small"
              type="info"
              @click="router.push({ path: '/user/login', query: { redirect: '/server' } })"
            >
              {{ t('maintenance.adminEntry') }}
            </ElButton>
          </div>
        </template>
      </ElResult>
      <ElResult
        v-else-if="phase === 'ended'"
        icon="success"
        :title="t('maintenance.ended.title')"
      >
        <template #extra>
          <div class="flex flex-col items-center gap-2">
            <ElButton type="primary" text bg @click="goHome">
              {{ t('maintenance.ended.enter') }}
            </ElButton>
          </div>
        </template>
      </ElResult>
      <ElResult v-else icon="info" :title="t('maintenance.notMaintaining')">
        <template #extra>
          <ElButton type="primary" text bg @click="goHome">
            {{ t('maintenance.ended.enter') }}
          </ElButton>
        </template>
      </ElResult>
    </ElCard>
  </div>
</template>

<style scoped></style>
