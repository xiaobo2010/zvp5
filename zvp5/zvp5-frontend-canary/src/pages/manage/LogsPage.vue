<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchLogs } from '@/shared/api/manage'
import type { AuditLogRow } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDateTime } from '@/shared/lib/format'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import AdaptivePagination from '@/shared/ui/pagination/AdaptivePagination.vue'
import { useAdaptivePagination } from '@/shared/composables/useAdaptivePagination'
import ManageTabs from './ManageTabs.vue'

const { t } = useI18n()

const typeMap: Record<string, string> = {
  all: '',
  security: 'security_alert,user_login,user_logout,user_verify,login_fail',
  write:
    'activity_update,notification_create,notification_update,notification_delete,notification_rollback,user_create,user_delete,roles_change,ban_change,group_create,group_update,group_delete,group_member_add,group_member_remove,password_change',
  system: 'retention_purge,user_trash_gc,device_unbind'
}

const page = ref(1)
const total = ref(0)
const query = ref('')
const group = ref('all')
const level = ref('all')
const rows = ref<AuditLogRow[]>([])
const loading = ref(true)
const error = ref('')
const listRef = ref<HTMLElement | null>(null)

const {
  size: perpage,
  auto,
  pageSizeOptions,
  setAuto,
  setPageSize
} = useAdaptivePagination({
  listId: 'manage-logs',
  container: listRef,
  itemSelector: '.row',
  defaultPageSize: 20,
  gap: 0,
  min: 5,
  reserve: 96,
  fallbackItemHeight: 68,
  max: 80
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / perpage.value)))

const groupOptions = computed(() => [
  { value: 'all', label: t('manage.logs.groups.all') },
  { value: 'security', label: t('manage.logs.groups.security') },
  { value: 'write', label: t('manage.logs.groups.write') },
  { value: 'system', label: t('manage.logs.groups.system') }
])

const levelOptions = computed(() => [
  { value: 'all', label: t('manage.logs.levelLabels.all') },
  { value: 'info', label: t('manage.logs.levelLabels.info') },
  { value: 'warning', label: t('manage.logs.levelLabels.warning') },
  { value: 'error', label: t('manage.logs.levelLabels.error') }
])

function tone(levelName: string): 'neutral' | 'warning' | 'danger' | 'info' {
  if (levelName === 'error') return 'danger'
  if (levelName === 'warning') return 'warning'
  if (levelName === 'debug') return 'info'
  return 'neutral'
}

function levelLabel(name: string): string {
  if (name === 'warning') return t('manage.logs.levelLabels.warning')
  if (name === 'error') return t('manage.logs.levelLabels.error')
  if (name === 'info') return t('manage.logs.levelLabels.info')
  return name
}

function timeOf(ts: number): string {
  if (!Number.isFinite(ts)) return '—'
  return formatDateTime(new Date(ts * 1000).toISOString())
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchLogs({
      page: page.value,
      perpage: perpage.value,
      query: query.value.trim(),
      type: typeMap[group.value] ?? '',
      level: level.value === 'all' ? '' : level.value
    })
    rows.value = result.data
    total.value = result.total
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (cause) {
    rows.value = []
    total.value = 0
    error.value = cause instanceof ApiError ? cause.message : t('common.error')
  } finally {
    loading.value = false
  }
}

let timer: number | undefined
function scheduleLoad() {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    page.value = 1
    void load()
  }, 340)
}

watch(query, scheduleLoad)
watch(group, () => {
  page.value = 1
  void load()
})
watch(level, () => {
  page.value = 1
  void load()
})
watch(page, () => void load())
watch(perpage, () => {
  if (page.value !== 1) {
    page.value = 1
    return
  }
  void load()
})

onMounted(load)
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1 class="title">{{ t('manage.logs.title') }}</h1>
        <p class="sub">{{ t('manage.logs.subtitle') }}</p>
      </div>
      <span class="range num">{{ total }}</span>
    </header>

    <ManageTabs />

    <div class="filters">
      <ZSegmented v-model="group" :options="groupOptions" size="sm" :aria-label="t('manage.logs.type')" />
      <ZSegmented v-model="level" :options="levelOptions" size="sm" :aria-label="t('manage.logs.level')" />
      <ZField
        v-model="query"
        class="search"
        type="search"
        icon="search"
        size="sm"
        :placeholder="t('manage.logs.searchPlaceholder')"
      />
    </div>

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <ZCard padding="none">
      <div v-if="loading && !rows.length" ref="listRef" class="rows">
        <div v-for="index in 6" :key="index" class="row">
          <ZSkeleton width="7rem" height="0.8rem" />
          <ZSkeleton width="45%" height="0.9rem" />
        </div>
      </div>

      <ul v-else-if="rows.length" ref="listRef" class="rows">
        <li v-for="row in rows" :key="row._id" class="row">
          <div class="stamp">
            <span class="time num">{{ timeOf(row.timestamp) }}</span>
            <span class="type num">{{ row.type }}</span>
          </div>
          <div class="main">
            <p class="data">{{ row.data }}</p>
            <small class="meta">
              {{ row.user_name || t('manage.logs.performer') }}
              <span v-if="row.client_ip" class="num"> · {{ row.client_ip }}</span>
              <span v-if="row.url" class="num"> · {{ row.url }}</span>
            </small>
          </div>
          <ZBadge :tone="tone(row.level)">{{ levelLabel(row.level) }}</ZBadge>
        </li>
      </ul>

      <ZEmpty v-else-if="!error" compact icon="book" :title="t('manage.logs.empty')" />
    </ZCard>

    <AdaptivePagination
      :page="page"
      :page-size="perpage"
      :total="total"
      :auto="auto"
      :page-size-options="pageSizeOptions"
      :disabled="loading"
      @update:page="page = $event"
      @update:page-size="setPageSize"
      @update:auto="setAuto"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
}

.title {
  font-size: var(--text-3xl);
  font-weight: 500;
}

.sub {
  margin-top: var(--space-2);
  color: var(--ink-muted);
}

.range {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.search {
  flex: 1;
  min-width: 220px;
  max-width: 320px;
  margin-left: auto;
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.row:last-child {
  border-bottom: 0;
}

.stamp {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  flex: none;
  width: 8.5rem;
}

.time {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.type {
  font-size: var(--text-2xs);
  color: var(--ink-muted);
  word-break: break-all;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  flex: 1;
  min-width: 0;
}

.data {
  font-size: var(--text-xs);
  color: var(--ink-soft);
  line-height: 1.6;
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.meta {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  word-break: break-all;
}
</style>
