<script setup lang="ts">
import { ref, toRefs, reactive, watch, computed } from 'vue'
import {
  ElCard,
  ElButton,
  ElDivider,
  ElSwitch,
  ElResult,
  ElTooltip,
  ElSkeleton,
  ElDialog, ElIcon } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { useI18n } from 'vue-i18n'
import { ZActivityMemberTimeJudge, ZDetailedAnalysis } from '..'
import { Refresh } from '@element-plus/icons-vue'
import api from '@/api'
import { Certificate, TableReport } from '@icon-park/vue-next'
import { pad } from '@/plugins/ua.ts'
import type { UserActivityTimeSums } from '@/../types'

const userStore = useUserStore()
const { t } = useI18n()
const loading = ref(false)
const error = ref('')

const props = withDefaults(
  defineProps<{
    user: string
    onCampus?: number
    offCampus?: number
    socialPractice?: number
    discount?: boolean
  }>(),
  {
    user: '',
    onCampus: 0,
    offCampus: 0,
    socialPractice: 0,
    discount: false
  }
)

const { user, onCampus, offCampus, socialPractice, discount } = toRefs(props)

const base = reactive({
  onCampus: 0,
  offCampus: 0,
  socialPractice: 0
})

const exceed = ref(discount.value)

function getDiscount(on: number, exceed: number, rate: number) {
  if (on <= exceed) return 0
  const result = Math.round((on - exceed) / rate)
  return result
}

base.onCampus = onCampus.value
base.offCampus = offCampus.value
base.socialPractice = socialPractice.value

// `/v2/users/{id}/time` is readable by the user itself, or by a secretary and
// above. Pre-judge only for UX; the backend remains the source of truth.
const canRead = computed(
  () =>
    user.value === userStore._id ||
    userStore.position.includes('admin') ||
    userStore.position.includes('department') ||
    userStore.position.includes('secretary')
)

const sums = computed<UserActivityTimeSums>(() => ({
  'on-campus': base.onCampus,
  'off-campus': base.offCampus,
  'social-practice': base.socialPractice
}))

async function getTime() {
  if (!user.value) return
  if (!canRead.value) {
    error.value = '无权限查看该用户时长'
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await api.user.time.read(user.value)
    if (result) {
      base.onCampus = result['on-campus']
      base.offCampus = result['off-campus']
      base.socialPractice = result['social-practice']
      off.value = base.offCampus + (exceed.value ? getDiscount(base.onCampus, 25, 3) : 0)
      on.value = base.onCampus + (exceed.value ? getDiscount(base.offCampus, 15, 2) : 0)
      if (user.value === userStore._id) {
        userStore.setTime(result)
      }
    }
  } catch {
    error.value = '时长数据加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

getTime()

const off = ref(base.offCampus)
const on = ref(base.onCampus)

watch(
  exceed,
  () => {
    off.value = base.offCampus + (exceed.value ? getDiscount(base.onCampus, 25, 3) : 0)
    on.value = base.onCampus + (exceed.value ? getDiscount(base.offCampus, 15, 2) : 0)
  },
  { immediate: true }
)

const reportPage = ref(false)
</script>

<template>
  <div class="judge-root">
    <ElDialog fullscreen destroy-on-close title="义工时长明细" v-model="reportPage">
      <ZDetailedAnalysis :id="user" mode="on-campus" :sums="sums" />
    </ElDialog>
    <ElCard
      shadow="never"
    >
      <ElRow>
        <ElCol :span="12">
          <span class="text-lg">{{ t('home.panels.time.title') }}</span>
        </ElCol>
        <ElCol :span="12" style="text-align: right">
          <ElTooltip content="查看时长明细" placement="top">
            <ElButton
              :icon="TableReport"
              text
              bg
              circle
              aria-label="查看时长明细"
              @click="reportPage = true"
            />
          </ElTooltip>
          <ElDivider v-if="user === userStore._id" direction="vertical" />
          <ElTooltip content="刷新时长" placement="top">
            <button
              class="yg2-icon-btn"
              type="button"
              :disabled="loading"
              title="刷新时长"
              aria-label="刷新时长"
              @click="getTime"
            >
              <ElIcon><Refresh /></ElIcon>
            </button>
          </ElTooltip>
        </ElCol>
      </ElRow>
      <ElResult v-if="error && !loading" icon="error" :title="error" />
      <div v-else-if="!loading" class="judge-grid">
        <div class="judge-cell">
          <ZActivityMemberTimeJudge type="on-campus" :realTime="on" />
        </div>
        <div class="judge-cell">
          <ZActivityMemberTimeJudge type="off-campus" :realTime="off" />
        </div>
        <div class="judge-cell">
          <ZActivityMemberTimeJudge type="social-practice" :realTime="base.socialPractice" />
        </div>
        <div class="judge-discount">
          <span>{{ t('home.panels.time.discount') }}</span>
          <ElSwitch v-model="exceed" aria-label="校内校外时长互补" />
        </div>
      </div>
      <ElSkeleton v-else :throttle="200" :rows="4" animated class="py-2" />
    </ElCard>
  </div>
</template>

<style scoped>
.judge-root {
  container-type: inline-size;
}

.judge-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
  gap: 16px 12px;
  padding: 8px 0 2px;
  text-align: center;
}

.judge-cell {
  min-width: 0;
}

.judge-discount {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-width: 0;
  margin-top: 2px;
  padding-top: 10px;
  border-top: 1px solid var(--yg2-sep);
  font-size: 12px;
  color: var(--yg2-text-secondary);
  text-align: center;
}

/* 卡片被压窄时（首页两栏布局的右列）把开关换到独立一行，
   三个环各占一列，避免环与开关互相挤压 */
@container (max-width: 560px) {
  .judge-grid {
    gap: 12px 8px;
  }
}

@media (max-width: 900px) {
  .judge-grid {
    gap: 14px 8px;
  }
}
</style>
