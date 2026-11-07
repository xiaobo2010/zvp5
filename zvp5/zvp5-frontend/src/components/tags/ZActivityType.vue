<script lang="ts" setup>
import { useI18n } from 'vue-i18n'
import { toRefs, computed } from 'vue'
import type { SpecialActivityClassification, ActivityStatus } from '@/../types'
import type { ActivityType as LegacyActivityType } from '@/../types'
import type { Activity } from '@/../types/v2'
import { ZButtonTag } from '@/components'
import { ZActivityStatus } from '@/components'
import { ElButtonGroup } from 'element-plus'
import classifications from './classifications'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    type: Activity['type'] | LegacyActivityType
    special?: SpecialActivityClassification
    showSpecial?: boolean
    size?: 'large' | 'default' | 'small'
    color?: boolean
    mode?: 'auto' | 'full' | 'icon'
    force?: 'full' | 'short'
    status?: Activity['status']
    bg?: boolean
    statusModifiable?: boolean
    callWhenModify?: boolean
    activity?: Activity
    refresh?: () => void
  }>(),
  {
    special: 'other',
    showSpecial: true,
    size: 'small',
    color: true,
    mode: 'auto',
    bg: true,
    statusModifiable: false,
    refresh: () => {},
    callWhenModify: false
  }
)

const {
  activity,
  type,
  size,
  mode,
  status,
  force,
  bg,
  statusModifiable,
  refresh,
  callWhenModify
} = toRefs(props)

// 兼容两套类型取值:V2 活动的 type 是 mode(on-campus 等),
// V1/后端实际返回的是义工类型(specified/special/social/scale),走另一套文案
const types = classifications.mode

const activityTypes = classifications.type

const isActivityType = typeof type?.value === 'string' && type.value in activityTypes

const effective = isActivityType || type?.value in types

const typeText = computed(() => {
  const raw = (type?.value as string) ?? ''
  const key = `activity.type.${raw}`
  const translated = t(key)
  return translated === key ? raw : translated
})
</script>

<template>
  <ElButtonGroup>
    <ZButtonTag
      v-if="type in types"
      :size="size ?? 'small'"
      :type="types[type].color"
      :tone="types[type].tone"
      :icon="types[type].icon"
      :unknown="!effective"
      :force="force"
      :bg="bg"
    >
      {{ t(`activity.mode.${type}.${mode === 'full' ? 'name' : 'short'}`) }}
    </ZButtonTag>
    <ZButtonTag
      v-else-if="isActivityType"
      :size="size ?? 'small'"
      :type="activityTypes[type].color"
      :icon="activityTypes[type].icon"
      :unknown="!effective"
      :force="force"
      :bg="bg"
    >
      {{ typeText }}
    </ZButtonTag>
    <ZActivityStatus
      v-if="status"
      :activity="activity"
      :type="status"
      :force="force"
      :modifiable="statusModifiable"
      :refresh="refresh"
      :call-when-modify="callWhenModify"
    />
  </ElButtonGroup>
</template>
