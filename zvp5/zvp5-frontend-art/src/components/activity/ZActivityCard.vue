<script setup lang="ts">
import { toRefs } from 'vue'
import api from '@/api'
import { ref } from 'vue'
import type { Activity, ActivityMember } from '@/../types/v2'
import { ElResult } from 'element-plus/es/components/result/index.mjs'
import { ElSkeleton } from 'element-plus/es/components/skeleton/index.mjs'
import { ZActivityDetails } from '.'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    _id: string
    userSession?: string
    mode?: 'mine' | 'class' | 'campus'
    perspective?: string // `mine` with other's user ObjectId
    showDetails?: boolean
    local?: boolean
  }>(),
  {
    mode: 'mine',
    perspective: 'mine',
    showDetails: true,
    local: false
  }
)
const emits = defineEmits<{
  refresh: []
}>()

const { t } = useI18n()

const { _id, mode, perspective, local, userSession } = toRefs(props)

const error = ref(false)
const loading = ref(true)
const membersCount = ref(0)
const activity = ref<Activity>()
const mine = ref<ActivityMember>()

api.activity.read
  .single(_id.value)
  .then((result) => {
    activity.value = result.activity as Activity
    membersCount.value = result.members_count
    if (mode.value === 'mine' && userSession.value) {
      api.activity.member
        .read(activity.value._id, userSession.value)
        .then((res) => {
          mine.value = res
          loading.value = false
        })
        .catch(() => {
          error.value = true
          loading.value = false
        })
    } else {
      loading.value = false
    }
  })
  .catch(() => {
    error.value = true
    loading.value = false
  })

const refresh = () => emits('refresh')
</script>

<template>
  <div v-if="!loading" v-loading="loading">
    <Transition v-if="!loading && !error && activity?._id" name="activity-card">
      <ZActivityDetails
        :activity="activity"
        :mode="mode"
        :members-count="membersCount"
        :member-details="mine"
        :perspective="perspective"
        @refresh="refresh"
        :show-details="showDetails"
        :local="local"
      />
    </Transition>
    <ElResult
      v-else-if="error"
      icon="error"
      status="error"
      title="出现错误"
    />
  </div>
  <ElSkeleton v-else v-model="loading" :rows="4" animated :throttle="500" />
</template>

<style scoped>
/* 原 animate.css 的 fadeIn/fadeOut 是 1s 的整棵子树淡入淡出；换成弹层时长档
   --yg-dur-slow（300ms），只为这张卡片保留进出场，不再全量引入 animate.css。 */
.activity-card-enter-active,
.activity-card-leave-active {
  transition: opacity var(--yg-dur-slow, 300ms) var(--yg-ease-out, ease);
}

.activity-card-enter-from,
.activity-card-leave-to {
  opacity: 0;
}
</style>
