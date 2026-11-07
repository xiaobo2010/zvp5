<script lang="ts" setup>
import type { Activity, ActivityMember } from '@/../types/v2'
import { Star } from '@icon-park/vue-next'
import { ZButtonTag } from '@/components'
import classifications from './classifications'
import { useI18n } from 'vue-i18n'
import { onMounted, ref, toRefs, watch } from 'vue'
import { useUserStore } from '@/stores/user'
import { ElPopover, ElProgress, ElRow, ElCol } from 'element-plus'
import api from '@/api'

const { t } = useI18n()
const user = useUserStore()

const targetsOfDuration = {
  'on-campus': 25,
  'off-campus': 15,
  'social-practice': 18
}

const props = withDefaults(
  defineProps<{
    mode: Activity['type']
    size?: 'large' | 'default' | 'small'
    force?: 'full' | 'short'
    bg?: boolean
    showMyDuration?: boolean
    showProperties?: boolean
  }>(),
  {
    size: 'small',
    bg: true,
    showMyDuration: false,
    showProperties: false
  }
)

const timeOfMine = ref({
  'on-campus': user.time.onCampus,
  'off-campus': user.time.offCampus,
  'social-practice': user.time.socialPractice
})
const loading = ref(false)

async function getTimeOfMine() {
  loading.value = true
  const response = await api.user.time.read(user._id)
  const percentage = await api.user.time.percentile(user._id)
  timeOfMine.value = response
  loading.value = false
}

const { mode, size, force, bg, showMyDuration, showProperties } = toRefs(props)

onMounted(() => {
  showMyDuration?.value && mode?.value && getTimeOfMine()
})

const percentOfMine = ref(
  mode?.value
    ? mode.value !== 'hybrid'
      ? Math.floor((timeOfMine.value[mode?.value] / targetsOfDuration[mode?.value]) * 100)
      : 0
    : 0
)

watch(timeOfMine, (newVal) => {
  percentOfMine.value = mode?.value
    ? mode.value !== 'hybrid'
      ? Math.floor((newVal[mode?.value] / targetsOfDuration[mode?.value]) * 100)
      : 0
    : 0

  if (percentOfMine.value > 100) {
    percentOfMine.value = 100
  }
})

watch(mode, (newVal) => {
  percentOfMine.value =
    newVal !== 'hybrid'
      ? Math.floor((timeOfMine.value[newVal as ActivityMember['mode']] / targetsOfDuration[newVal as ActivityMember['mode']]) * 100)
      : 0

  if (percentOfMine.value > 100) {
    percentOfMine.value = 100
  }
})

// 颜色/图标映射与 ZActivityType 共用同一份，避免两处模式配色漂移
const modes = classifications.mode
</script>

<template>
  <ElPopover
    :title="t(`activity.mode.${mode}.name`)"
    :width="160"
    :trigger="showMyDuration || showProperties ? 'hover' : 'contextmenu'"
    v-if="mode !== 'hybrid'"
  >
    <template #reference>
      <ZButtonTag
        :size="size ?? 'small'"
        :type="modes[mode].color"
        :tone="modes[mode].tone"
        :icon="modes[mode].icon"
        :unknown="!mode || !(mode in modes)"
        :force="force"
        :bg="bg"
      >
        {{ t(`activity.mode.${mode}.name`) }}
      </ZButtonTag>
    </template>
    <ElRow>
      <ElCol :span="24" class="py-1" v-if="showMyDuration && mode">
        <ElProgress
          type="dashboard"
          :percentage="percentOfMine"
          :status="percentOfMine >= 100 ? 'success' : ''"
        >
          <p class="text-2xl">{{ percentOfMine }}%</p>
          <p v-if="timeOfMine[mode] < targetsOfDuration[mode]" class="text-sm">
            {{ timeOfMine[mode] }} h / {{ targetsOfDuration[mode] }} h
          </p>
          <p v-else>{{ timeOfMine[mode] }} h</p>
        </ElProgress>
      </ElCol>
    </ElRow>
  </ElPopover>
  <ZButtonTag v-else :size="size ?? 'small'" type="info" :icon="Star" :force="force" :bg="bg">
    {{ t(`activity.mode.hybrid.name`) }}
  </ZButtonTag>
</template>
