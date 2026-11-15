<script lang="ts" setup>
import type { Activity, ActivityMember } from '@/../types/v2'
import { toRefs, ref, computed, defineAsyncComponent } from 'vue'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElPopconfirm } from 'element-plus/es/components/popconfirm/index.mjs'
import { ElButtonGroup } from 'element-plus/es/components/button/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { Calendar, ArrowRight, Delete, Edit, MagicStick } from '@element-plus/icons-vue'
import dayjs from '@/plugins/dayjs'
import { useUserStore } from '@/stores/user'
import ZSceneRunDialog from '@/components/server/ZSceneRunDialog.vue'
import { buildActivityContextMarkdown } from '@/utils/activityAnalysis'
import {
  ZActivityDuration,
  ZActivityMember,
  ZActivityType,
  ZButtonOrCard,
  ZActivityMemberList
} from '@/components'
import { ZMarkdown, ZMarkdownEditor } from '@/components/markdown'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import router from '@/router'
import ZActivityAttachmentsDialog from './ZActivityAttachmentsDialog.vue'
// 活动统计弹窗是 chart.js 的唯一活动侧入口：打开「统计」才需要，按需加载。
const ZActivityStatsDialog = defineAsyncComponent(
  () => import('./ZActivityStatsDialog.vue')
)
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
    showMembersInline?: boolean
  }>(),
  {
    mode: 'mine',
    perspective: 'mine',
    showDetails: false,
    local: false,
    showMembersInline: false,
    membersCount: 0
  }
)
const emits = defineEmits<{
  refresh: []
}>()

const user = useUserStore()
const { t } = useI18n()
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
const canViewStats = computed(
  () =>
    user.position.includes('admin') ||
    user.position.includes('department') ||
    user.position.includes('secretary')
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

// AI 分析：活动标识、基本信息与聚合统计作为参考内容交给场景会话。
// 统计与组织人是「锦上添花」：取不到就降级成占位文案，不挡入口。
const analysisVisible = ref(false)
const analysisContext = ref('')
const analysisLoading = ref(false)

async function openActivityAnalysis() {
  if (analysisLoading.value) {
    return
  }
  analysisLoading.value = true
  try {
    const aid = activity.value._id
    const [detail, stats, creator] = await Promise.allSettled([
      api.activity.read.single(aid),
      api.activity.stats.analysis(aid),
      api.user.readOne(activity.value.creator)
    ])
    if (detail.status === 'rejected' || stats.status === 'rejected') {
      ElMessage({
        message: t('activity.analysis.partialFailed'),
        type: 'warning',
        grouping: true,
        plain: true
      })
    }
    analysisContext.value = buildActivityContextMarkdown({
      activity: activity.value,
      membersCount: detail.status === 'fulfilled' ? detail.value.members_count : null,
      creatorName: creator.status === 'fulfilled' ? creator.value?.name : null,
      statistics: stats.status === 'fulfilled' ? stats.value : null
    })
    analysisVisible.value = true
  } finally {
    analysisLoading.value = false
  }
}
</script>

<template>
  <ZButtonOrCard
    mode="card"
    @mouseover="hovered = true"
    @mouseleave="hovered = false"
    style="width: 100%"
  >
    <div class="yg-activity-details">
      <!-- 标题行 -->
      <div class="yg-activity-details__head">
        <h3 class="yg-activity-details__title">
          <span
            v-if="!editDescription"
            class="break-words"
            @dblclick="canModify && (editDescription = true)"
          >
            {{ activity.name }}
          </span>
          <ElInput
            v-else
            v-model="name"
            class="yg-activity-details__name-input"
            required
            @keydown.enter="submitInfo"
          />
        </h3>
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
      </div>

      <!-- 描述 -->
      <div v-if="!editDescription" class="yg-activity-details__desc-row">
        <div
          class="yg-activity-details__desc"
          @dblclick="canModify && (editDescription = true)"
        >
          <ZMarkdown :source="activity.description" />
        </div>
        <button
          v-if="canModify"
          type="button"
          class="yg-icon-btn yg-icon-btn--ghost yg-icon-btn--sm flex-shrink-0"
          :title="t('activity.form.actions.modify')"
          :aria-label="t('activity.form.actions.modify')"
          @click="editDescription = true"
        >
          <ElIcon><Edit /></ElIcon>
        </button>
      </div>
      <div v-else class="yg-activity-details__edit">
        <ZMarkdownEditor v-model="description" :maxlength="1000" />
        <div class="yg-activity-details__edit-actions">
          <ElButton
            text
            circle
            class="px-2"
            type="primary"
            :icon="ArrowRight"
            @click="submitInfo"
          />
        </div>
      </div>

      <!-- 元信息行 -->
      <div class="yg-activity-details__meta">
        <ElTag :type="getDateStatusColor(activity.date)" size="small">
          <ElIcon class="mr-1"><Calendar /></ElIcon>
          {{ dayjs(activity.date).format('YYYY-MM-DD HH:mm:ss') }}
        </ElTag>
        <ZActivityDuration
          class="px-2"
          v-if="memberDetails"
          :mode="memberDetails?.mode"
          :duration="memberDetails?.duration"
          force="full"
        />
        <ZActivityMemberList
          v-if="!showMembersInline"
          :members-count="membersCount"
          class="px-2"
          :activity="activity"
          @refresh="refresh"
        />
        <ZActivityStatsDialog v-if="canViewStats" :aid="activity._id" @refresh="refresh" />
        <ElButton
          v-if="canModify"
          class="px-2"
          text
          bg
          round
          size="small"
          type="primary"
          :icon="MagicStick"
          :loading="analysisLoading"
          :disabled="analysisLoading"
          :aria-label="t('activity.analysis.action')"
          :title="t('activity.analysis.hint')"
          @click="openActivityAnalysis"
        >
          {{ t('activity.analysis.action') }}
        </ElButton>
        <ZActivityAttachmentsDialog
          v-if="canModify"
          class="px-2"
          :activity-id="activity._id"
        />
      </div>

      <!-- 底部：创建者 + 危险操作 -->
      <div class="yg-activity-details__foot">
        <ZActivityMember
          :id="activity.creator"
          :icon="
            StreamlineInterfaceUserEditActionsCloseEditGeometricHumanPencilPersonSingleUpUserWrite
          "
        />
        <ElPopconfirm
          v-if="
            mode !== 'mine' &&
            !local &&
            (user._id === activity.creator || user.position.includes('admin') || user.position.includes('department'))
          "
          :title="t('activity.form.actions.delete.confirm')"
          width="328px"
          confirm-button-type="danger"
          @confirm="deleteActivity(activity._id)"
        >
          <template #reference>
            <!-- 删除活动是高危操作：红字按钮 + 二次确认（准则第 5 章） -->
            <ElButton :icon="Delete" type="danger" size="small" text>
              {{ t('activity.form.actions.delete.name') }}
            </ElButton>
          </template>
        </ElPopconfirm>
      </div>

      <ZSceneRunDialog
        v-model="analysisVisible"
        scene="activity"
        :title="t('activity.analysis.title')"
        :context="analysisContext"
      />
    </div>
  </ZButtonOrCard>
</template>

<style scoped>
/* 活动卡片：统一内边距与行距，替代原先零散的 pl-4 硬编码（准则 2.3 间距阶梯） */
.yg-activity-details {
  display: flex;
  flex-direction: column;
  gap: var(--yg2-sp-3);
  padding: var(--yg2-sp-4);
}

.yg-activity-details__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg2-sp-2);
}

.yg-activity-details__title {
  margin: 0;
  letter-spacing: -0.02em;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.25;
  color: var(--yg2-text);
}

.yg-activity-details__name-input {
  width: 328px;
}

.yg-activity-details__desc {
  font-size: 14px;
  color: var(--yg2-text-secondary);
}

.yg-activity-details__desc-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--yg2-sp-2);
}

.yg-activity-details__edit-actions {
  display: flex;
  justify-content: flex-end;
  padding-block: var(--yg2-sp-2);
}

.yg-activity-details__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg2-sp-2);
}

.yg-activity-details__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yg2-sp-2);
  padding-top: var(--yg2-sp-2);
  border-top: calc(var(--yg2-sp-1) / 4) solid var(--yg2-line);
}
</style>
