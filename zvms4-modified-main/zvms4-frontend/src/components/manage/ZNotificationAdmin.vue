<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWindowSize } from '@vueuse/core'
import {
  ElButton,
  ElCard,
  ElDialog,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElIcon,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { Timer } from '@element-plus/icons-vue'
import type { FormInstance, FormItemRule, FormRules, TableInstance } from 'element-plus'
import dayjs from '@/plugins/dayjs'
import { ZMarkdownEditor } from '@/components/markdown'
import ZNotificationHistoryDialog from './ZNotificationHistoryDialog.vue'
import {
  createNotification,
  deleteNotification,
  listAll,
  updateNotification,
  type NotificationRecord,
  type UpdateNotificationInput
} from '@/api/notifications'
import {
  createChangelog,
  deleteChangelog,
  getChangelog,
  updateChangelog,
  type ChangelogEntry
} from '@/api/changelog'

const { t } = useI18n()

const { width, height } = useWindowSize()

/** 窄屏（≤768px，准则 3.7）：列宽收窄，减少横向滚动距离 */
const narrow = computed(() => width.value <= 768)

const tableRef = ref<TableInstance>()

/** 分页行 + 卡片下边距 + 页面底部留白 */
const BOTTOM_ROOM = 96

/**
 * 表格高度 = 视口高度 − 表格实际顶部 − 底部预留（与群组列表同一算法）。
 * 表格带 max-height：纵向滚动交给 EP 的内部滚动容器，表头固定在表格顶部。
 */
function computeTableHeight(): number {
  const el = (tableRef.value as { $el?: HTMLElement } | undefined)?.$el
  const top = el?.getBoundingClientRect?.().top ?? 0
  if (top > 0 && top < height.value * 0.8) {
    return Math.max(280, Math.round(height.value - top - BOTTOM_ROOM))
  }
  return Math.max(280, Math.round(height.value - 320))
}

const tableHeight = ref(computeTableHeight())

watch([width, height], () => {
  tableHeight.value = computeTableHeight()
})

onMounted(() => {
  void nextTick(() => {
    tableHeight.value = computeTableHeight()
  })
})

const TITLE_MAX = 200
const CONTENT_MAX = 4000
const CONTENT_BYTE_MAX = 4000

const rows = ref<NotificationRecord[]>([])
const loading = ref(false)
const loadFailed = ref(false)
const togglingId = ref('')
const deletingId = ref('')
const historyVisible = ref(false)
const historyRow = ref<NotificationRecord | null>(null)

function openHistory(row: NotificationRecord) {
  historyRow.value = row
  historyVisible.value = true
}

type DialogMode = 'create' | 'edit'
const mode = ref<DialogMode>('create')
const dialogVisible = ref(false)
const editingId = ref('')
const editingCreatedAt = ref<string | null>(null)
const submitting = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  title: '',
  content: ''
})

function utf8Bytes(value: string): number {
  try {
    return new TextEncoder().encode(value).length
  } catch {
    // 极老环境没有 TextEncoder 时按 UTF-8 最坏情况估算，避免误放行。
    return value.length * 4
  }
}

const contentBytes = computed(() => utf8Bytes(form.content.trim()))

function validateTitle(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const title = String(value ?? '').trim()
  if (!title) {
    callback(new Error(t('manage.notifications.validation.titleRequired')))
    return
  }
  if (title.length > TITLE_MAX) {
    callback(new Error(t('manage.notifications.validation.titleTooLong', { max: TITLE_MAX })))
    return
  }
  callback()
}

function validateContent(_rule: unknown, value: unknown, callback: (error?: Error) => void) {
  const content = String(value ?? '').trim()
  if (!content) {
    callback(new Error(t('manage.notifications.validation.contentRequired')))
    return
  }
  if (content.length > CONTENT_MAX) {
    callback(new Error(t('manage.notifications.validation.contentTooLong', { max: CONTENT_MAX })))
    return
  }
  if (utf8Bytes(content) > CONTENT_BYTE_MAX) {
    callback(
      new Error(t('manage.notifications.validation.contentTooManyBytes', { max: CONTENT_BYTE_MAX }))
    )
    return
  }
  callback()
}

const formRules: FormRules = {
  title: [{ validator: validateTitle, trigger: 'blur' }] as FormItemRule[],
  content: [{ validator: validateContent, trigger: 'blur' }] as FormItemRule[]
}

async function loadNotifications() {
  if (loading.value) {
    return
  }
  loading.value = true
  try {
    const data = await listAll()
    rows.value = data.notifications
    loadFailed.value = false
  } catch {
    rows.value = []
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value
}

function stripMarkdown(source: string): string {
  // 先整段删掉围栏代码，再删未闭合的围栏残留（否则代码正文会混进摘要）
  const withoutCodeBlocks = source
    .replace(/```[\s\S]*?```/g, '')
    .replace(/^```.*$/gm, '')
  return withoutCodeBlocks
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s{0,3}[-*+]\s+/gm, '')
    .replace(/^\s{0,3}\d+\.\s+/gm, '')
    .replace(/^\s{0,3}([-*_]\s?){3,}\s*$/gm, '')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/(\*\*\*|\*\*|\*|___|__|_|~~)([^*_~]+?)\1/g, '$2')
    .replace(/\s+/g, ' ')
    .trim()
}

function preview(content: string): string {
  const compact = stripMarkdown(content)
  return compact.length > 120 ? `${compact.slice(0, 120)}…` : compact
}

function replaceRow(updated: NotificationRecord) {
  const index = rows.value.findIndex((row) => row.id === updated.id)
  if (index >= 0) {
    rows.value[index] = updated
  } else {
    rows.value.unshift(updated)
  }
}

function openCreate() {
  mode.value = 'create'
  editingId.value = ''
  editingCreatedAt.value = null
  form.title = ''
  form.content = ''
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row: NotificationRecord) {
  mode.value = 'edit'
  editingId.value = row.id
  editingCreatedAt.value = row.created_at
  form.title = row.title
  form.content = row.content
  dialogVisible.value = true
  void nextTick(() => formRef.value?.clearValidate())
}

async function confirmSend(): Promise<boolean> {
  try {
    await ElMessageBox.confirm(
      t('manage.notifications.sendConfirm'),
      t('manage.notifications.sendConfirmTitle'),
      {
        confirmButtonText: t('manage.notifications.sendConfirmOk'),
        cancelButtonText: t('manage.notifications.sendConfirmCancel'),
        type: 'warning',
        closeOnClickModal: false
      }
    )
    return true
  } catch {
    return false
  }
}

async function submit() {
  if (submitting.value || !formRef.value) {
    return
  }
  // 从校验前就占用提交态：确认框打开期间第二次点击也不会再发一次创建请求。
  submitting.value = true
  try {
    const valid = await formRef.value.validate().catch(() => false)
    if (!valid) {
      return
    }

    const title = form.title.trim()
    const content = form.content.trim()

    if (mode.value === 'create') {
      if (!(await confirmSend())) {
        return
      }
      const data = await createNotification({ title, content })
      replaceRow(data.notification)
      dialogVisible.value = false
      ElMessage.success(t('manage.notifications.createdNotice'))
      return
    }

    const original = rows.value.find((row) => row.id === editingId.value)
    if (!original) {
      dialogVisible.value = false
      return
    }
    const payload: UpdateNotificationInput = {}
    if (title !== original.title) {
      payload.title = title
    }
    if (content !== original.content) {
      payload.content = content
    }
    if (Object.keys(payload).length === 0) {
      ElMessage.info(t('manage.notifications.noChanges'))
      dialogVisible.value = false
      return
    }

    const data = await updateNotification(original.id, payload)
    replaceRow(data.notification)
    dialogVisible.value = false
    ElMessage.success(t('manage.notifications.savedNotice'))
  } catch {
    // axios 拦截器已给出后端 422/403 等错误提示，保留弹窗内容供修改与重试。
  } finally {
    submitting.value = false
  }
}

async function toggleActive(row: NotificationRecord) {
  if (togglingId.value) {
    return
  }
  togglingId.value = row.id
  const next = !row.active
  try {
    const data = await updateNotification(row.id, { active: next })
    replaceRow(data.notification)
    ElMessage.success(
      next ? t('manage.notifications.activatedNotice') : t('manage.notifications.deactivatedNotice')
    )
  } catch {
    // 保持原状态；错误提示由 axios 拦截器统一给出。
  } finally {
    togglingId.value = ''
  }
}

async function remove(row: NotificationRecord) {
  if (deletingId.value) {
    return
  }
  // 提前占位：确认框打开时按钮进入 loading，重复点击不会叠加第二个确认框。
  deletingId.value = row.id
  try {
    await ElMessageBox.confirm(
      t('manage.notifications.deleteConfirm', { title: row.title }),
      t('manage.notifications.deleteConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.notifications.delete'),
        cancelButtonText: t('manage.notifications.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    deletingId.value = ''
    return
  }

  try {
    await deleteNotification(row.id)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    ElMessage.success(t('manage.notifications.deletedNotice'))
  } catch {
    // 404/权限错误由 axios 统一提示。
  } finally {
    deletingId.value = ''
  }
}

const dialogTitle = computed(() =>
  mode.value === 'create'
    ? t('manage.notifications.dialog.createTitle')
    : t('manage.notifications.dialog.editTitle')
)

const changelogRows = ref<ChangelogEntry[]>([])
const changelogLoading = ref(false)
const changelogLoadFailed = ref(false)
const changelogDialogVisible = ref(false)
const changelogSaving = ref(false)
const changelogDeletingId = ref('')
const changelogEditingId = ref('')
const changelogSaveFailed = ref(false)
const changelogForm = reactive({
  version: '',
  date: '',
  items: ''
})

const changelogDialogTitle = computed(() =>
  changelogEditingId.value
    ? t('manage.notifications.changelog.dialog.editTitle')
    : t('manage.notifications.changelog.dialog.createTitle')
)

const changelogCanSave = computed(
  () => Boolean(changelogForm.version.trim()) && Boolean(changelogForm.date.trim())
)

async function loadChangelog() {
  changelogLoading.value = true
  try {
    const data = await getChangelog()
    changelogRows.value = data ?? []
    changelogLoadFailed.value = !data
  } catch {
    changelogRows.value = []
    changelogLoadFailed.value = true
  } finally {
    changelogLoading.value = false
  }
}

function openChangelogCreate() {
  changelogEditingId.value = ''
  changelogForm.version = ''
  changelogForm.date = ''
  changelogForm.items = ''
  changelogSaveFailed.value = false
  changelogDialogVisible.value = true
}

function openChangelogEdit(entry: ChangelogEntry) {
  changelogEditingId.value = entry.id
  changelogForm.version = entry.version
  changelogForm.date = entry.date
  changelogForm.items = entry.items.join('\n')
  changelogSaveFailed.value = false
  changelogDialogVisible.value = true
}

function parseChangelogItems(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

function changelogFirstItem(entry: ChangelogEntry): string {
  return entry.items[0] ?? '—'
}

async function submitChangelog() {
  if (changelogSaving.value || !changelogCanSave.value) {
    return
  }
  changelogSaving.value = true
  changelogSaveFailed.value = false
  try {
    const payload = {
      version: changelogForm.version.trim(),
      date: changelogForm.date.trim(),
      items: parseChangelogItems(changelogForm.items)
    }
    if (changelogEditingId.value) {
      await updateChangelog(changelogEditingId.value, payload)
    } else {
      await createChangelog(payload)
    }
    changelogDialogVisible.value = false
    ElMessage.success(t('manage.notifications.changelog.saved'))
    await loadChangelog()
    void loadNotifications()
  } catch {
    changelogSaveFailed.value = true
    ElMessage.error(t('manage.notifications.changelog.saveFailed'))
  } finally {
    changelogSaving.value = false
  }
}

async function removeChangelog(entry: ChangelogEntry) {
  if (changelogDeletingId.value) {
    return
  }
  changelogDeletingId.value = entry.id
  try {
    await ElMessageBox.confirm(
      t('manage.notifications.changelog.deleteConfirm'),
      t('manage.notifications.changelog.title'),
      {
        type: 'warning',
        confirmButtonText: t('manage.notifications.delete'),
        cancelButtonText: t('manage.notifications.cancel'),
        confirmButtonClass: 'el-button--danger',
        closeOnClickModal: false
      }
    )
  } catch {
    changelogDeletingId.value = ''
    return
  }

  try {
    await deleteChangelog(entry.id)
    changelogRows.value = changelogRows.value.filter((row) => row.id !== entry.id)
    ElMessage.success(t('manage.notifications.changelog.deleted'))
    void loadNotifications()
  } catch {
    // 404/权限错误由 axios 统一提示。
  } finally {
    changelogDeletingId.value = ''
  }
}

onMounted(() => {
  void loadNotifications()
  void loadChangelog()
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-col gap-1">
            <span>{{ t('manage.notifications.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--yg-text-tertiary)">
              {{ t('manage.notifications.subtitle') }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <ElButton @click="loadNotifications">
              {{ t('manage.notifications.refresh') }}
            </ElButton>
            <ElButton type="primary" @click="openCreate">
              {{ t('manage.notifications.create') }}
            </ElButton>
          </div>
        </div>
      </template>

      <!-- 加载遮罩只盖数据区：卡片头按钮与底部提示保持可见 -->
      <ElTable
        ref="tableRef"
        v-loading="loading"
        :data="rows"
        row-key="id"
        stripe
        :max-height="tableHeight"
      >
        <ElTableColumn
          prop="title"
          :label="t('manage.notifications.columns.title')"
          :min-width="narrow ? 140 : 200"
          show-overflow-tooltip
        />
        <ElTableColumn
          prop="content"
          :label="t('manage.notifications.columns.content')"
          :min-width="narrow ? 160 : 260"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span>{{ preview(row.content) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="author_name"
          :label="t('manage.notifications.columns.author')"
          :min-width="narrow ? 100 : 120"
        >
          <template #default="{ row }">
            <div class="flex flex-col">
              <span>{{ row.author_name || '—' }}</span>
              <span class="text-xs tabular-nums" style="color: var(--yg-text-tertiary)">
                {{ row.author_id }}
              </span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="created_at"
          :label="t('manage.notifications.columns.createdAt')"
          :min-width="narrow ? 140 : 160"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ formatTime(row.created_at) }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          prop="active"
          :label="t('manage.notifications.columns.status')"
          width="100"
          align="center"
        >
          <template #default="{ row }">
            <ElTag :type="row.active ? 'primary' : 'info'" size="small">
              {{
                row.active
                  ? t('manage.notifications.status.active')
                  : t('manage.notifications.status.inactive')
              }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('manage.notifications.columns.actions')"
          :min-width="narrow ? 150 : 210"
          fixed="right"
          align="right"
        >
          <template #default="{ row }">
            <div class="flex flex-wrap justify-end gap-1">
              <ElButton text type="primary" size="small" @click="openEdit(row)">
                {{ t('manage.notifications.edit') }}
              </ElButton>
              <ElButton text size="small" @click="openHistory(row)">
                <ElIcon class="mr-1"><Timer /></ElIcon>
                {{ t('manage.notifications.history.entry') }}
              </ElButton>
              <ElButton
                text
                size="small"
                :loading="togglingId === row.id"
                :disabled="Boolean(togglingId) && togglingId !== row.id"
                @click="toggleActive(row)"
              >
                {{
                  row.active
                    ? t('manage.notifications.deactivate')
                    : t('manage.notifications.activate')
                }}
              </ElButton>
              <ElButton
                text
                type="danger"
                size="small"
                :loading="deletingId === row.id"
                :disabled="Boolean(deletingId) && deletingId !== row.id"
                @click="remove(row)"
              >
                {{ t('manage.notifications.delete') }}
              </ElButton>
            </div>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :description="
              loadFailed ? t('manage.notifications.loadFailed') : t('manage.notifications.empty')
            "
            :image-size="100"
          />
        </template>
      </ElTable>
      <p class="text-xs mt-2" style="color: var(--yg-text-tertiary)">
        {{ t('manage.notifications.deleteHint') }}
      </p>
    </ElCard>

    <ElCard shadow="never">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-col gap-1">
            <span>{{ t('manage.notifications.changelog.title') }}</span>
            <span class="text-xs font-normal" style="color: var(--yg-text-tertiary)">
              {{ t('manage.notifications.changelog.hint') }}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <ElButton @click="loadChangelog">
              {{ t('manage.notifications.refresh') }}
            </ElButton>
            <ElButton type="primary" @click="openChangelogCreate">
              {{ t('manage.notifications.changelog.add') }}
            </ElButton>
          </div>
        </div>
      </template>

      <ElTable
        v-loading="changelogLoading"
        :data="changelogRows"
        row-key="id"
        stripe
        max-height="320"
      >
        <ElTableColumn
          prop="version"
          :label="t('manage.notifications.changelog.columns.version')"
          width="140"
          show-overflow-tooltip
        />
        <ElTableColumn
          prop="date"
          :label="t('manage.notifications.changelog.columns.date')"
          width="120"
        >
          <template #default="{ row }">
            <span class="tabular-nums">{{ row.date }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('manage.notifications.changelog.columns.items')"
          :min-width="narrow ? 180 : 320"
        >
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <span class="changelog-item-preview">{{ changelogFirstItem(row) }}</span>
              <span
                v-if="row.items.length > 1"
                class="shrink-0 text-xs"
                style="color: var(--yg-text-tertiary)"
              >
                {{
                  t('manage.notifications.changelog.moreItems', { count: row.items.length - 1 })
                }}
              </span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn
          :label="t('manage.notifications.changelog.columns.actions')"
          width="150"
          fixed="right"
          align="right"
        >
          <template #default="{ row }">
            <div class="flex flex-wrap justify-end gap-1">
              <ElButton text type="primary" size="small" @click="openChangelogEdit(row)">
                {{ t('manage.notifications.edit') }}
              </ElButton>
              <ElButton
                text
                type="danger"
                size="small"
                :loading="changelogDeletingId === row.id"
                :disabled="Boolean(changelogDeletingId) && changelogDeletingId !== row.id"
                @click="removeChangelog(row)"
              >
                {{ t('manage.notifications.delete') }}
              </ElButton>
            </div>
          </template>
        </ElTableColumn>
        <template #empty>
          <div class="flex flex-col items-center gap-2 py-3">
            <ElEmpty
              :description="
                changelogLoadFailed
                  ? t('manage.notifications.loadFailed')
                  : t('manage.notifications.changelog.empty')
              "
              :image-size="72"
            />
            <ElButton v-if="changelogLoadFailed" size="small" @click="loadChangelog">
              {{ t('manage.notifications.refresh') }}
            </ElButton>
          </div>
        </template>
      </ElTable>
    </ElCard>

    <ElDialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="min(880px, 94vw)"
      top="5vh"
      :close-on-click-modal="false"
      :close-on-press-escape="!submitting"
      :show-close="!submitting"
      append-to-body
    >
      <ElForm
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-position="top"
        class="notification-form"
      >
        <ElFormItem :label="t('manage.notifications.dialog.titleLabel')" prop="title">
          <ElInput
            v-model="form.title"
            :maxlength="TITLE_MAX"
            show-word-limit
            :placeholder="t('manage.notifications.dialog.titlePlaceholder')"
            :disabled="submitting"
          />
        </ElFormItem>
        <ElFormItem :label="t('manage.notifications.dialog.contentLabel')" prop="content">
          <div
            class="w-full"
            :style="submitting ? 'pointer-events: none; opacity: 0.6;' : ''"
          >
            <ZMarkdownEditor
              v-model="form.content"
              :maxlength="CONTENT_MAX"
              :show-count="false"
              :placeholder="t('manage.notifications.dialog.contentPlaceholder')"
            />
          </div>
          <div class="flex flex-wrap justify-between gap-2 w-full text-xs mt-1">
            <span style="color: var(--yg-text-tertiary)">
              {{ t('manage.notifications.dialog.contentHint') }}
            </span>
            <span class="tabular-nums flex gap-3" style="color: var(--yg-text-tertiary)">
              <span :style="form.content.length > CONTENT_MAX ? 'color: var(--el-color-danger)' : ''">
                {{ t('manage.notifications.dialog.charCount', { current: form.content.length, max: CONTENT_MAX }) }}
              </span>
              <span :style="contentBytes > CONTENT_BYTE_MAX ? 'color: var(--el-color-danger)' : ''">
                {{ t('manage.notifications.dialog.byteCount', { bytes: contentBytes, max: CONTENT_BYTE_MAX }) }}
              </span>
            </span>
          </div>
        </ElFormItem>
      </ElForm>

      <template #footer>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span class="text-xs" style="color: var(--yg-text-tertiary)">
            {{
              mode === 'create'
                ? t('manage.notifications.dialog.createHint')
                : t('manage.notifications.dialog.editHint', { time: formatTime(editingCreatedAt) })
            }}
          </span>
          <div class="flex flex-wrap items-center justify-end gap-2">
            <ElButton :disabled="submitting" @click="dialogVisible = false">
              {{ t('manage.notifications.cancel') }}
            </ElButton>
            <ElButton type="primary" :loading="submitting" @click="submit">
              {{
                mode === 'create'
                  ? t('manage.notifications.dialog.send')
                  : t('manage.notifications.dialog.save')
              }}
            </ElButton>
          </div>
        </div>
      </template>
    </ElDialog>

    <ZNotificationHistoryDialog
      :notification="historyRow"
      :visible="historyVisible"
      @update:visible="historyVisible = $event"
      @refresh="loadNotifications"
    />

    <ElDialog
      v-model="changelogDialogVisible"
      :title="changelogDialogTitle"
      width="min(640px, 94vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!changelogSaving"
      :show-close="!changelogSaving"
      append-to-body
    >
      <ElForm
        :model="changelogForm"
        label-position="top"
        class="notification-form"
      >
        <ElFormItem :label="t('manage.notifications.changelog.dialog.version')" required>
          <ElInput
            v-model="changelogForm.version"
            :maxlength="32"
            show-word-limit
            :placeholder="t('manage.notifications.changelog.dialog.versionPlaceholder')"
            :disabled="changelogSaving"
          />
        </ElFormItem>
        <ElFormItem :label="t('manage.notifications.changelog.dialog.date')" required>
          <ElInput
            v-model="changelogForm.date"
            :maxlength="16"
            placeholder="YYYY-MM-DD"
            :disabled="changelogSaving"
          />
        </ElFormItem>
        <ElFormItem :label="t('manage.notifications.changelog.dialog.items')">
          <ElInput
            v-model="changelogForm.items"
            type="textarea"
            :rows="8"
            :placeholder="t('manage.notifications.changelog.dialog.itemsHint')"
            :disabled="changelogSaving"
          />
        </ElFormItem>
      </ElForm>

      <template #footer>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span
            v-if="changelogSaveFailed"
            class="text-xs"
            style="color: var(--el-color-danger)"
          >
            {{ t('manage.notifications.changelog.saveFailed') }}
          </span>
          <span v-else class="text-xs" style="color: var(--yg-text-tertiary)">
            {{ t('manage.notifications.changelog.dialog.itemsHint') }}
          </span>
          <div class="flex flex-wrap items-center justify-end gap-2">
            <ElButton :disabled="changelogSaving" @click="changelogDialogVisible = false">
              {{ t('manage.notifications.cancel') }}
            </ElButton>
            <ElButton
              type="primary"
              :loading="changelogSaving"
              :disabled="!changelogCanSave"
              @click="submitChangelog"
            >
              {{ t('manage.notifications.changelog.dialog.save') }}
            </ElButton>
          </div>
        </div>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.notification-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.changelog-item-preview {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
</style>
