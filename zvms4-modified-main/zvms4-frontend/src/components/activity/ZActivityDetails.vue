<script lang="ts" setup>
import type { Activity, ActivityMember } from '@/../types/v2'
import { toRefs, ref, computed } from 'vue'
import {
  ElButton,
  ElInput,
  ElRow,
  ElCol,
  ElPopconfirm,
  ElButtonGroup,
  ElMessage
} from 'element-plus'
import categories from './categories.json'
import { Calendar, ArrowRight, Delete, Plus, Edit } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import { useUserStore } from '@/stores/user'
import {
  ZActivityDuration,
  ZActivityMember,
  ZActivityType,
  ZButtonOrCard,
  ZActivityMemberList,
  ZActivityStatsDialog
} from '@/components'
import { ZMarkdown, ZMarkdownEditor } from '@/components/markdown'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import router from '@/router'
import { StreamlineInterfaceUserEditActionsCloseEditGeometricHumanPencilPersonSingleUpUserWrite } from '@/icons'

const props = withDefaults(
  defineProps<{
    activity: Activity
    memberDetails?: ActivityMember
    membersCount?: number
    mode?: 'mine' | 'class' | 'campus' | 'register'
    perspective?: string // `mine` with other's user ObjectId
    showDetails?: boolean
    local?: boolean
  }>(),
  {
    mode: 'mine',
    perspective: 'mine',
    showDetails: false,
    local: false,
    membersCount: 0
  }
)
const emits = defineEmits<{
  refresh: []
}>()

const user = useUserStore()
const { t, locale } = useI18n()
const { activity, mode, membersCount, showDetails, local, memberDetails } = toRefs(props)

const hovered = ref(false)

function getDateStatusColor(date: string) {
  const now = dayjs()
  const target = dayjs(date)
  if (target.isAfter(now, 'date')) return 'success'
  if (target.isBefore(now, 'date')) return 'warning'
  return 'primary'
}

const editName = ref(false)
const name = ref(activity.value.name)
const editDescription = ref(false)
const description = ref(activity.value.description)
const canModify = computed(
  () => user.position.includes('admin') || user.position.includes('department')
)

async function submitInfo() {
  try {
    const result = await api.activity.update.info(activity.value._id, name.value, description.value)
    if (result?.status === 'error') {
      ElMessage.error(result.message || t('activity.form.saveFailed'))
      return
    }
  } catch {
    return
  }
  editDescription.value = false
  editName.value = false
  activity.value.description = description.value
  activity.value.name = name.value
  emits('refresh')
}

async function deleteActivity(id: string) {
  await api.activity.deleteOne(id, user._id)
  router.push('/activities')
  emits('refresh')
}

const refresh = () => emits('refresh')
</script>

<template>
  <ZButtonOrCard
    mode="card"
    @mouseover="hovered = true"
    @mouseleave="hovered = false"
    style="width: 100%"
  >
    <p class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xl pl-4" style="width: 100%">
      <span v-if="!editDescription" @dblclick="canModify && (editDescription = true)" class="break-words">{{
        activity.name
      }}</span>
      <ElInput v-else v-model="name" style="width: 328px" required @keydown.enter="submitInfo">
        <!--<template #append>
          <ElButton class="px-2" type="success" :icon="ArrowRight" @click="submitInfo" />
        </template>-->
      </ElInput>
      <ElButtonGroup>
        <ZActivityType
          class="px-2"
          :type="activity.type"
          mode="full"
          show-special
          force="full"
          :status="activity.status"
        />
      </ElButtonGroup>
    </p>
    <div v-if="!editDescription" class="flex items-start justify-between gap-2 pl-4">
      <div class="text-sm text-gray-500" @dblclick="canModify && (editDescription = true)">
        <ZMarkdown :source="activity.description" />
      </div>
      <ElButton
        v-if="canModify"
        class="flex-shrink-0"
        size="small"
        text
        bg
        circle
        :icon="Edit"
        :title="t('activity.form.actions.modify')"
        @click="editDescription = true"
      />
    </div>
    <div v-else class="pt-2 pl-4">
      <ZMarkdownEditor v-model="description" :maxlength="1000" />
      <div style="text-align: right" class="py-2">
        <ElButton
          text
          bg
          circle
          class="px-2"
          type="success"
          :icon="ArrowRight"
          @click="submitInfo"
        />
      </div>
    </div>
    <div class="py-2 pl-4">
      <ElButton
        size="small"
        text
        bg
        round
        :type="getDateStatusColor(activity.date)"
        class="py-2"
        :icon="Calendar"
      >
        {{ dayjs(activity.date).format('YYYY-MM-DD HH:mm:ss') }}
      </ElButton>
      <ZActivityDuration
        class="px-2"
        v-if="memberDetails"
        :mode="memberDetails?.mode"
        :duration="memberDetails?.duration"
        force="full"
      />
      <ZActivityMemberList
        :members-count="membersCount"
        class="px-2"
        :activity="activity"
        @refresh="refresh"
      />
      <ZActivityStatsDialog :aid="activity._id" @refresh="refresh" />
    </div>
    <ElRow>
      <ElCol :span="6">
        <div class="pl-4 py-2">
          <ZActivityMember
            :id="activity.creator"
            :icon="
              StreamlineInterfaceUserEditActionsCloseEditGeometricHumanPencilPersonSingleUpUserWrite
            "
          />
        </div>
      </ElCol>
      <ElCol :span="18">
        <div class="pl-4 py-2" style="text-align: right">
          <ElPopconfirm
            v-if="
              mode !== 'mine' &&
              !local &&
              (user._id === activity.creator || user.position.includes('admin') || user.position.includes('department'))
            "
            :title="t('activity.form.actions.delete.confirm')"
            width="328px"
            @confirm="deleteActivity(activity._id)"
          >
            <template #reference>
              <ElButton :icon="Delete" type="danger" size="small" text bg round>
                {{ t('activity.form.actions.delete.name') }}
              </ElButton>
            </template>
          </ElPopconfirm>
        </div>
      </ElCol>
    </ElRow>
  </ZButtonOrCard>
</template>
