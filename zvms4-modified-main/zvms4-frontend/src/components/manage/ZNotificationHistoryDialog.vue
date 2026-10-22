<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElButton, ElCheckbox, ElDialog, ElEmpty, ElMessage, ElMessageBox, ElTag } from 'element-plus'
import dayjs from '@/plugins/dayjs'
import { ZMarkdown } from '@/components/markdown'
import { diffLines, diffViewRows, hasChanges, type DiffViewRow } from '@/utils/textDiff'
import {
  httpStatusOf,
  listVersions,
  rollbackNotification,
  type NotificationRecord,
  type NotificationVersionItem,
  type NotificationVersionsData
} from '@/api/notifications'

const props = defineProps<{
  notification: NotificationRecord | null
  visible: boolean
}>()

const emit = defineEmits<{
  (event: 'update:visible', value: boolean): void
  (event: 'refresh'): void
}>()

const { t } = useI18n()

const loading = ref(false)
const failed = ref(false)
const failedStatus = ref<number | null>(null)
const versions = ref<NotificationVersionItem[]>([])
const currentVersion = ref(0)
const selectedVersionId = ref<number | null>(null)
const compareVisible = ref(true)
const rollingBack = ref(false)
let requestSeq = 0

const selected = computed<NotificationVersionItem | null>(() => {
  const id = selectedVersionId.value
  if (id === null) {
    return null
  }
  return versions.value.find((item) => item.version === id) ?? null
})

const currentSnapshot = computed<NotificationVersionItem | null>(() => {
  return versions.value.find((item) => item.version === currentVersion.value) ?? null
})

const titleIdentical = computed(() => {
  const item = selected.value
  const current = currentSnapshot.value
  if (!item || !current || item.version === current.version) {
    return true
  }
  return !hasChanges(item.title, current.title)
})

const identical = computed(() => {
  const item = selected.value
  const current = currentSnapshot.value
  if (!item || !current || item.version === current.version) {
    return true
  }
  if (item.content_hash && current.content_hash) {
    return item.content_hash === current.content_hash
  }
  return titleIdentical.value && !hasChanges(item.content, current.content)
})

const bodyRows = computed<DiffViewRow[]>(() => {
  const item = selected.value
  const current = currentSnapshot.value
  if (!item || !current || identical.value) {
    return []
  }
  return diffViewRows(diffLines(item.content, current.content))
})

const titleRows = computed(() => {
  if (titleIdentical.value) {
    return []
  }
  const item = selected.value
  const current = currentSnapshot.value
  if (!item || !current) {
    return []
  }
  return diffLines(item.title, current.title)
})

const canRollback = computed(() => !identical.value && !rollingBack.value && !loading.value)

function shortHash(hash: string | undefined): string {
  return hash ? hash.slice(0, 8) : '—'
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value
}

const OP_TAG_TYPES: Record<NotificationVersionItem['op'], 'success' | 'primary' | 'danger' | 'warning'> = {
  create: 'success',
  update: 'primary',
  delete: 'danger',
  rollback: 'warning'
}

type FailReason = 'notFound' | 'forbidden' | 'network' | 'unknown'

const failDescription = computed(() => {
  const status = failedStatus.value
  const key: FailReason =
    status === 404 ? 'notFound' : status === 403 ? 'forbidden' : status === null ? 'network' : 'unknown'
  return t(`manage.notifications.history.${key}`)
})

async function loadHistory() {
  const notification = props.notification
  if (!notification) {
    return
  }
  const seq = ++requestSeq
  loading.value = true
  failed.value = false
  try {
    const data: NotificationVersionsData = await listVersions(notification.id)
    if (seq !== requestSeq) {
      return
    }
    versions.value = data.versions
    currentVersion.value = data.version
    const target = data.versions.find((item) => item.version === data.version) ?? data.versions[0] ?? null
    selectedVersionId.value = target ? target.version : null
  } catch (error) {
    if (seq !== requestSeq) {
      return
    }
    versions.value = []
    selectedVersionId.value = null
    currentVersion.value = 0
    failed.value = true
    failedStatus.value = httpStatusOf(error)
  } finally {
    if (seq === requestSeq) {
      loading.value = false
    }
  }
}

async function rollback() {
  const item = selected.value
  const notification = props.notification
  if (!item || !notification || rollingBack.value || identical.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      t('manage.notifications.history.rollbackConfirm', { version: item.version }),
      t('manage.notifications.history.rollbackConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.notifications.history.rollbackConfirmOk'),
        cancelButtonText: t('manage.notifications.cancel'),
        closeOnClickModal: false
      }
    )
  } catch {
    return
  }

  rollingBack.value = true
  try {
    const data = await rollbackNotification(notification.id, item.version)
    ElMessage.success(t('manage.notifications.history.rollbackDone', { version: data.version }))
    emit('refresh')
    await loadHistory()
  } catch (error) {
    const status = httpStatusOf(error)
    if (status === 403) {
      ElMessage.error(t('manage.notifications.history.rollbackForbidden'))
    } else if (status === 404) {
      ElMessage.error(t('manage.notifications.history.rollbackMissing'))
    } else if (status === null) {
      ElMessage.error(t('manage.notifications.history.rollbackNetwork'))
    } else {
      ElMessage.error(t('manage.notifications.history.rollbackFailed'))
    }
  } finally {
    rollingBack.value = false
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      compareVisible.value = true
      void loadHistory()
    }
  }
)
</script>

<template>
  <ElDialog
    :model-value="visible"
    :title="t('manage.notifications.history.title')"
    width="min(960px, 94vw)"
    top="5vh"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="loading" class="history-body">
      <ElEmpty
        v-if="!loading && failed"
        :description="failDescription"
        :image-size="90"
      >
        <ElButton size="small" type="primary" @click="loadHistory">
          {{ t('manage.notifications.refresh') }}
        </ElButton>
      </ElEmpty>

      <ElEmpty
        v-else-if="!loading && versions.length === 0"
        :description="t('manage.notifications.history.empty')"
        :image-size="90"
      />

      <div v-else class="history-layout">
        <aside class="history-list">
          <button
            v-for="item in versions"
            :key="item.version"
            type="button"
            class="history-item"
            :class="{ active: selectedVersionId === item.version }"
            @click="selectedVersionId = item.version"
          >
            <span class="history-item-top">
              <span class="history-version">v{{ item.version }}</span>
              <code class="history-hash">{{ shortHash(item.content_hash) }}</code>
              <ElTag
                v-if="item.op === 'rollback' && item.source_version !== null"
                type="warning"
                size="small"
              >
                {{
                  t('manage.notifications.history.ops.rollbackFrom', {
                    version: item.source_version
                  })
                }}
              </ElTag>
              <ElTag v-else :type="OP_TAG_TYPES[item.op]" size="small">
                {{ t(`manage.notifications.history.ops.${item.op}`) }}
              </ElTag>
            </span>
            <span class="history-item-meta">
              <span>{{ item.author_name || '—' }}</span>
              <span class="tabular-nums">{{ formatTime(item.created_at) }}</span>
            </span>
          </button>
        </aside>

        <section v-if="selected" class="history-detail">
          <div class="history-detail-head">
            <ElCheckbox v-model="compareVisible">
              {{
                compareVisible
                  ? t('manage.notifications.history.compareOn')
                  : t('manage.notifications.history.compareOff')
              }}
            </ElCheckbox>
          </div>
          <h3 class="history-detail-title">{{ selected.title }}</h3>

          <template v-if="compareVisible">
            <div v-if="identical" class="history-same">
              {{ t('manage.notifications.history.identical') }}
            </div>
            <div v-else class="history-diff">
              <template v-if="titleRows.length > 0">
                <div
                  v-for="(row, index) in titleRows"
                  :key="`t${index}`"
                  class="diff-row"
                  :class="row.type === 'del' ? 'diff-del' : 'diff-add'"
                >
                  {{ row.text }}
                </div>
              </template>
              <template v-for="(row, index) in bodyRows" :key="`b${index}`">
                <div v-if="row.type === 'collapsed'" class="diff-row diff-collapsed">
                  {{ t('manage.notifications.history.collapsedLines', { count: row.count }) }}
                </div>
                <div
                  v-else
                  class="diff-row"
                  :class="{
                    'diff-same': row.type === 'same',
                    'diff-add': row.type === 'add',
                    'diff-del': row.type === 'del'
                  }"
                >
                  {{ row.text }}
                </div>
              </template>
            </div>
          </template>
          <ZMarkdown v-else :source="selected.content" />
        </section>
      </div>
    </div>

    <template #footer>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <span class="text-xs" style="color: var(--zv-label)">
          {{ t('manage.notifications.history.rollbackHint') }}
        </span>
        <div class="flex items-center gap-2">
          <ElButton @click="emit('update:visible', false)">
            {{ t('manage.notifications.cancel') }}
          </ElButton>
          <ElButton
            type="primary"
            :loading="rollingBack"
            :disabled="!canRollback"
            @click="rollback"
          >
            {{
              canRollback && selected
                ? t('manage.notifications.history.rollbackTo', { version: selected.version })
                : t('manage.notifications.history.rollback')
            }}
          </ElButton>
        </div>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.history-body {
  min-height: 320px;
}

.history-layout {
  display: flex;
  gap: 16px;
  min-height: 400px;
}

.history-list {
  width: 300px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 60vh;
  overflow-y: auto;
}

.history-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.history-item:hover {
  background: var(--el-fill-color-light);
}

.history-item.active {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-fill-color-light);
}

.history-item-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.history-version {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--el-color-primary);
}

.history-hash {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--zv-label);
  background: var(--el-fill-color-darker);
  border-radius: 4px;
  padding: 0 4px;
}

.history-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.history-detail-head {
  display: flex;
  justify-content: flex-end;
}

.history-detail-title {
  margin: 0;
  font-size: 16px;
}

.history-same {
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  color: var(--zv-label);
  font-size: 13px;
}

.history-diff {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 60vh;
  overflow-y: auto;
}

.diff-row {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.diff-same {
  color: var(--zv-label);
}

.diff-add {
  background: rgba(103, 194, 58, 0.15);
}

.diff-del {
  background: rgba(245, 108, 108, 0.18);
  text-decoration: line-through;
}

.diff-collapsed {
  color: var(--zv-label);
  background: var(--el-fill-color-lighter);
  font-size: 12px;
  text-align: center;
}

@media (max-width: 720px) {
  .history-layout {
    flex-direction: column;
  }

  .history-list {
    width: 100%;
    flex-direction: row;
    max-height: none;
    overflow-x: auto;
    overflow-y: hidden;
    padding-bottom: 4px;
  }

  .history-item {
    width: 240px;
    flex: none;
    border: 1px solid var(--el-border-color-lighter);
  }
}
</style>
