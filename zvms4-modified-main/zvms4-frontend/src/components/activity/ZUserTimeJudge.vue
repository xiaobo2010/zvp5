<script setup lang="ts">
import { ref, toRefs, reactive, watch, computed } from 'vue'
import {
  ElCard,
  ElRow,
  ElCol,
  ElButton,
  ElDivider,
  ElSwitch,
  ElResult,
  ElTooltip,
  ElSkeleton,
  ElDialog
} from 'element-plus'
import { useWindowSize } from '@vueuse/core'
import { useUserStore } from '@/stores/user'
import { useI18n } from 'vue-i18n'
import { ZActivityMemberTimeJudge, ZDetailedAnalysis } from '..'
import { Refresh } from '@element-plus/icons-vue'
import api from '@/api'
import { Certificate, TableReport } from '@icon-park/vue-next'
import { pad } from '@/plugins/ua.ts'
import type { UserActivityTimeSums } from '@/../types'

const { width, height } = useWindowSize()
const userStore = useUserStore()
const { t } = useI18n()
const loading = ref(false)
const error = ref('')
const isWide = computed(() => width.value >= 900 && width.value >= height.value)

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
  <div>
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
            <ElButton
              type="success"
              :disabled="loading"
              :icon="Refresh"
              text
              bg
              circle
              aria-label="刷新时长"
              @click="getTime"
            />
          </ElTooltip>
        </ElCol>
      </ElRow>
      <ElResult v-if="error && !loading" icon="error" :title="error" />
      <div v-else-if="!loading" class="fill py-2 statistic">
        <div v-if="isWide" class="statistic__wide">
          <div class="statistic__gauges">
            <div class="statistic__gauge">
              <ZActivityMemberTimeJudge type="on-campus" :realTime="on" />
            </div>
            <div class="statistic__gauge">
              <ZActivityMemberTimeJudge type="off-campus" :realTime="off" />
            </div>
            <div class="statistic__gauge">
              <ZActivityMemberTimeJudge type="social-practice" :realTime="base.socialPractice" />
            </div>
          </div>
          <div class="statistic__discount">
            <span>{{ t('home.panels.time.discount') }}</span>
            <ElSwitch v-model="exceed" aria-label="校内校外时长互补" />
          </div>
        </div>
        <div v-else class="statistic__stack">
          <ZActivityMemberTimeJudge type="on-campus" :realTime="on" />
          <ElDivider class="statistic__divider" />
          <ZActivityMemberTimeJudge type="off-campus" :realTime="off" />
          <ElDivider class="statistic__divider" />
          <ZActivityMemberTimeJudge type="social-practice" :realTime="base.socialPractice" />
          <ElDivider class="statistic__divider" />
          <div class="statistic__discount">
            <span>{{ t('home.panels.time.discount') }}</span>
            <ElSwitch v-model="exceed" aria-label="校内校外时长互补" />
          </div>
        </div>
      </div>
      <ElSkeleton v-else :throttle="200" :rows="4" animated class="py-2" />
    </ElCard>
  </div>
</template>

<style scoped>
.statistic {
  text-align: center;
}

.statistic__wide {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(120px, 160px);
  align-items: center;
  column-gap: var(--yg-space-6);
}

.statistic__gauges {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: center;
}

.statistic__gauge {
  display: flex;
  justify-content: center;
  min-width: 0;
  padding-inline: var(--yg-space-3);
}

.statistic__gauge + .statistic__gauge {
  border-left: 1px solid var(--yg-border-subtle);
}

.statistic__stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 360px;
  margin-inline: auto;
}

.statistic__divider {
  width: 100%;
  margin: var(--yg-space-2) 0;
}

.statistic__divider.el-divider--horizontal {
  border-top-color: var(--yg-border-subtle);
}

.statistic__discount {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yg-space-2);
}
</style>
