<script lang="ts" setup>
import type { CreateActivityType } from '@/../types'
import type { Activity, ActivityMember } from '@/../types/v2'
import { reactive, ref, toRefs, watch, computed } from 'vue'
import dayjs from '@/plugins/dayjs'
import { useI18n } from 'vue-i18n'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElCol } from 'element-plus/es/components/col/index.mjs'
import { ElDatePicker } from 'element-plus/es/components/date-picker/index.mjs'
import { ElForm } from 'element-plus/es/components/form/index.mjs'
import { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import { ElRadio } from 'element-plus/es/components/radio/index.mjs'
import { ElRadioGroup } from 'element-plus/es/components/radio/index.mjs'
import { ElRow } from 'element-plus/es/components/row/index.mjs'
import { ElScrollbar } from 'element-plus/es/components/scrollbar/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElCascader } from 'element-plus/es/components/cascader/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { useWindowSize } from '@vueuse/core'
import { ArrowLeft, ArrowRight, Location } from '@element-plus/icons-vue'
import {
  ZActivityDetails,
  ZActivityMemberList,
  ZSelectActivityMode,
  ZSelectPerson
} from '@/components'
import { ZMarkdownEditor } from '@/components/markdown'
import api from '@/api'
import { validateActivity } from './validation'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import categories from './categories.json'
import { composeDurationRecommendation } from './activityCategory'
import ZAttachmentUpload from '@/components/attachment/ZAttachmentUpload.vue'

const { t, locale } = useI18n()
const { height } = useWindowSize()
const router = useRouter()
const load = ref(false)
const userStore = useUserStore()

const props = defineProps<{
  type: CreateActivityType
}>()

const { type } = toRefs(props)

const activePage = ref<'info' | 'member' | 'volunteer_order' | 'review'>('info')

const canManageAttachments = computed(
  () => userStore.position.includes('admin') || userStore.position.includes('department')
)

const activity = reactive<Activity>({
  _id: '',
  type: '' as unknown as Activity['type'],
  name: '',
  description: '',
  date: '',
  createdAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  updatedAt: dayjs().format('YYYY-MM-DD HH:mm:ss'),
  creator: userStore._id,
  status: 'pending',
  place: '',
  appointee: userStore._id,
  approver: 'authority',
  origin: 'other'
})

const createdId = ref<string | void>('')

const token = localStorage.getItem('token')
const remindText = ref(
  locale.value === 'zh-CN' ? '请先选择义工来源' : 'Please select a volunteer source first'
)
const remindCategory = ref<string[]>([])

const approveStudent = ref('')

const activityCategories =
  Object.getOwnPropertyDescriptor(categories, locale.value)?.value ?? categories['zh-CN']

const members = reactive<ActivityMember[]>([])

// 表单区可视高度：视口高度减去外壳（顶栏 + 内容区内边距）与卡片操作区，
// 让表单在卡片内滚动、操作按钮始终可见。
const CHROME_HEIGHT = 56 + 48 + 88
const scrollableCardHeight = computed(() => Math.max(240, height.value - CHROME_HEIGHT))

const origin = ref<string[]>(['others'])

// @ts-ignore
watch(origin, (newVal) => {
  while (remindCategory.value.length > 0) {
    remindCategory.value.pop()
  }
  const val = Array.from(newVal)
  // @ts-ignore
  let reminder = { children: categories[locale.value] }
  if (
    newVal.includes('off-campus') ||
    newVal.includes('social-practice') ||
    newVal.includes('others')
  ) {
    remindText.value =
      locale.value === 'zh-CN'
        ? '请确保已提交相关纸质材料并由团委审核通过。'
        : 'Please ensure that relevant paper materials have been submitted and approved by the Youth League Committee.'
    remindCategory.value = [
      // @ts-ignore
      categories[locale.value].find((item) => item.value === newVal[0])?.label
    ]
    return
  }
  while (val.length > 0) {
    const target = val.shift()
    // @ts-ignore
    const intermediate = reminder.children.find((item) => item.value === target) ?? reminder
    remindCategory.value.push(intermediate?.label ?? '')
    // @ts-ignore
    reminder = intermediate
  }
  const duration =
    // @ts-ignore
    reminder?.duration ??
    ({} as Record<
      ActivityMember['mode'] | 'hybrid',
      { lte?: number; gte?: number; unlimited?: boolean }
    >)
  const dict = {
    'on-campus':
      locale.value === 'zh-CN'
        ? '仅可计入校内义工。'
        : ' Could only count as on-campus volunteer work.',
    'off-campus':
      locale.value === 'zh-CN'
        ? '仅可计入校外义工。'
        : ' Could only count as off-campus volunteer work.',
    'social-practice':
      locale.value === 'zh-CN' ? '仅可计入社会实践。' : ' Could only count as social practice.',
    hybrid:
      locale.value === 'zh-CN'
        ? '可按个人选择计入模式。'
        : ' Could select record mode according to personal choice.'
  }
  // @ts-ignore
  const [mode, config] = Object.entries(duration)[0]
  remindText.value =
    composeDurationRecommendation({
      // @ts-ignore
      ...config,
      documented: newVal.includes('content-clubs'),
      lang: locale.value,
      ticketRequired: newVal.includes('student-activities') || newVal.includes('awards'),
      // @ts-ignore
      attachment: reminder?.attachment ?? 0
      // @ts-ignore
    }) + dict[mode]
})

async function nextStep() {
  load.value = true
  if (activePage.value === 'info') {
    activity.origin = 'other'
    const result = await api.activity.insert(activity).catch(() => undefined)
    if (typeof result === 'string' && result) {
      createdId.value = result
      activity._id = result
      activePage.value = 'member'
    } else {
      load.value = false
      if (result && typeof result !== 'string') {
        ElMessage.error(result.message || t('activity.form.saveFailed'))
      }
      return
    }
  } else if (activePage.value === 'member') {
    activePage.value = 'volunteer_order'
  } else if (activePage.value === 'volunteer_order') {
  /*  activePage.value = 'review'
  } else if (activePage.value === 'review') {
    if (checkReviewAllowed()) {
      await api.activity.update.status(createdId.value, 'effective')
    }*/
    await router.push(`/activity/details/${activity._id}`)
  }
  load.value = false
}

function prevStep() {
  if (activePage.value === 'volunteer_order') {
    activePage.value = 'member'
  } else if (activePage.value === 'review') {
    activePage.value = 'member'
  }
}

const validated = ref(false)

watch(
  () => activity,
  () => {
    validated.value = validateActivity(
      activity,
      activePage.value === 'volunteer_order' ? 'review' : activePage.value
    )
  },
  { deep: true, immediate: true }
)

function checkReviewAllowed() {
  if (userStore.position.includes('admin')) {
    return true
  }
  if (userStore.position.includes('department')) {
    if (activity.approver === 'authority') {
      return true
    } else {
      return userStore._id == activity.approver
    }
  }
  return false
}
</script>

<template>
  <div class="yg-activity-create">
    <section class="yg-card yg-activity-create__card">
      <ElForm label-position="top" :model="activity" class="w-full">
        <ElScrollbar :height="scrollableCardHeight + 'px'">
          <div class="yg-activity-create__body">
            <ElFormItem
              prop="name"
              :label="t('activity.form.name')"
              required
              :rules="[{ required: true, message: t('validation.create.name.required') }]"
              v-if="activePage === 'info'"
            >
              <ElInput v-model="activity.name" />
            </ElFormItem>
            <ElFormItem :label="t('activity.form.description')" v-if="activePage === 'info'">
              <ZMarkdownEditor
                v-model="activity.description"
                :maxlength="1000"
                :placeholder="t('activity.form.descriptionPlaceholder')"
              />
            </ElFormItem>
            <ElFormItem
              v-if="activePage === 'info'"
              :label="t('activity.form.type')"
              required
              prop="type"
            >
              <ZSelectActivityMode v-model="activity.type" show-hybrid class="w-full" />
            </ElFormItem>
            <ElFormItem
              :label="t('activity.form.date')"
              required
              prop="date"
              :rules="[{ required: true, message: t('validation.create.date.required') }]"
              v-if="activePage === 'info'"
            >
              <ElDatePicker class="full" style="width: 100%" v-model="activity.date" />
            </ElFormItem>
            <!--<ElFormItem
              v-if="activePage === 'info'"
              :label="t('activity.origins.name')"
              required
              :rules="[{ required: true, message: t('validation.create.classify.required') }]"
            >
              <ElCascader v-model="origin" :options="activityCategories" class="w-full" />
            </ElFormItem>-->
            <!--<ElFormItem
              v-if="activePage === 'info'"
              :label="t('activity.registration.location')"
              prop="place"
            >
              <ElInput :prefix-icon="Location" v-model="activity.place" />
            </ElFormItem>-->
            <!--<ElFormItem
              v-if="activePage === 'info'"
              required
              :label="t('activity.registration.approver')"
              style="width: 100%"
            >
              <ElRow style="width: 100%">
                <ElCol :span="16">
                  <ElRadioGroup v-model="activity.approver">
                    <ElRadio border value="authority">
                      {{ t('activity.registration.approvers.authority') }}
                    </ElRadio>
                    <ElRadio border value="member">
                      {{ t('activity.registration.approvers.member') }}
                    </ElRadio>
                  </ElRadioGroup>
                </ElCol>
                <ElCol :span="8" style="width: 100%" v-if="activity.approver === 'member'">
                  <ZSelectPerson style="width: 100%" v-model="approveStudent" :filter-start="6" />
                </ElCol>
              </ElRow>
            </ElFormItem>-->
            <!--<ElFormItem v-if="activePage === 'member' || activePage === 'info'">
              <ElAlert
                :title="remindCategory.join(' / ')"
                :description="remindText"
                type="info"
                :closable="false"
                class="mb-4"
              />
            </ElFormItem>-->
            <ElFormItem
              :label="t('activity.form.person', members.length)"
              v-if="activePage === 'member'"
            >
              <div class="w-full" style="flex-grow: 1">
                <ZActivityMemberList mode="card" :activity="activity as unknown as Activity" />
                <p class="yg-activity-create__hint">{{ t('nav.registerHint') }}</p>
              </div>
            </ElFormItem>
            <ElFormItem
              :label="t('activity.form.volunteerOrder.name')"
              v-if="activePage === 'volunteer_order'"
              style="width: 100%"
            >
              <div class="w-full" style="flex-grow: 1">
                <ElAlert
                  v-if="!activity._id"
                  :title="t('activity.form.volunteerOrder.needCreateInfo')"
                  type="warning"
                  :closable="false"
                />
                <ElAlert
                  v-else-if="!canManageAttachments"
                  :title="t('activity.form.volunteerOrder.noPermission')"
                  type="warning"
                  :closable="false"
                />
                <ZAttachmentUpload
                  v-else
                  entity-type="activity"
                  :entity-oid="activity._id"
                />
              </div>
            </ElFormItem>
            <ElFormItem label="Preview" v-if="activePage === 'review'" style="width: 100%">
              <p class="text-center text-xl py-6">
                {{ t('activity.creating_guide.check.created') }}
              </p>
              <div class="w-full" style="flex-grow: 1">
                <ZActivityDetails :activity="activity as unknown as Activity" mode="campus" />
              </div>
              <p class="py-6">
                <span v-if="checkReviewAllowed()">
                  {{ t('activity.creating_guide.check.allowed') }}
                </span>
                <span v-else>
                  {{ t('activity.creating_guide.check.disallowed') }}
                </span>
              </p>
            </ElFormItem>
          </div>
        </ElScrollbar>
        <div class="yg-activity-create__actions">
          <p class="yg-text-sm yg-text-tertiary" v-if="activePage === 'info'">
            {{ t('activity.creating_guide.info.next') }}
          </p>
          <div class="flex items-center justify-end gap-2">
            <!-- 次级操作：描边按钮；主操作：唯一的天蓝实心按钮（准则 3.5） -->
            <ElButton
              v-if="activePage !== 'info'"
              :icon="ArrowLeft"
              :disabled="activePage === 'member'"
              @click="prevStep"
            >
              {{ t('activity.batch.steps.prev') }}
            </ElButton>
            <ElButton
              :disabled="!validated"
              type="primary"
              :icon="ArrowRight"
              :loading="load"
              @click="nextStep"
            >
              {{ activePage === 'member' || activePage === 'volunteer_order' ? '完成' : t('activity.batch.steps.next') }}
            </ElButton>
          </div>
        </div>
      </ElForm>
    </section>
  </div>
</template>

<style scoped>
/* 创建向导：卡片内表单滚动，操作区常驻卡片底部 */
.yg-activity-create {
  padding: var(--yg-space-6) 0;
}

.yg-activity-create__card {
  width: 100%;
}

.yg-activity-create__body {
  padding: var(--yg-space-4) var(--yg-space-5) 0;
}

.yg-activity-create__actions {
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-2);
  padding: var(--yg-space-3) var(--yg-space-5) var(--yg-space-4);
  border-top: calc(var(--yg-space-1) / 4) solid var(--yg-border-subtle);
}

.yg-activity-create__body :deep(.el-form-item) {
  margin-bottom: var(--yg-space-5);
}

.yg-activity-create__body :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.yg-activity-create__hint {
  margin-top: var(--yg-space-2);
  color: var(--yg-text-tertiary);
  font-size: var(--yg-font-size-xs);
  line-height: var(--yg-line-height-normal);
}
</style>
