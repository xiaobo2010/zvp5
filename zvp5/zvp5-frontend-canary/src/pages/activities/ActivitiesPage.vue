<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchActivities } from '@/shared/api/domain'
import type { Activity } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDate } from '@/shared/lib/format'
import ZCard from '@/shared/ui/ZCard.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZButton from '@/shared/ui/ZButton.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import AdaptivePagination from '@/shared/ui/pagination/AdaptivePagination.vue'
import { useAdaptivePagination } from '@/shared/composables/useAdaptivePagination'

const { t } = useI18n()

const type = ref('all')
const status = ref('all')
const search = ref('')
const page = ref(1)
const listRef = ref<HTMLElement | null>(null)
const total = ref(0)
const items = ref<Activity[]>([])
const loading = ref(true)
const error = ref('')

const {
  size,
  auto,
  density,
  columns,
  effectiveColumns,
  pageSizeOptions,
  setAuto,
  setPageSize,
  setColumns
} = useAdaptivePagination({
    listId: 'activities',
    container: listRef,
    itemSelector: '.card',
    gridSelector: '.grid',
    defaultPageSize: 12,
    allowColumns: true,
    defaultColumns: 2,
    min: 2,
    gap: 16,
    reserve: 96,
    pageSizeOptions: [4, 6, 10, 20, 40]
  })

const typeOptions = computed(() => [
  { value: 'all', label: t('common.all') },
  { value: 'on-campus', label: t('activities.type.on-campus') },
  { value: 'off-campus', label: t('activities.type.off-campus') },
  { value: 'social-practice', label: t('activities.type.social-practice') }
])

const statusOptions = computed(() => [
  { value: 'all', label: t('common.all') },
  { value: 'effective', label: t('activities.status.effective') },
  { value: 'pending', label: t('activities.status.pending') },
  { value: 'refused', label: t('activities.status.refused') }
])

function statusTone(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'effective') return 'success'
  if (status === 'pending') return 'warning'
  return 'danger'
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))
const rangeLabel = computed(() => {
  if (!total.value) return '0'
  const start = (page.value - 1) * size.value + 1
  const end = Math.min(total.value, page.value * size.value)
  return `${start}–${end} / ${total.value}`
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchActivities({
      page: page.value,
      size: size.value,
      query: search.value.trim(),
      type: type.value === 'all' ? '' : type.value,
      status: status.value === 'all' ? '' : status.value
    })
    items.value = result.activities
    total.value = result.total
    if (page.value > totalPages.value) page.value = totalPages.value
  } catch (cause) {
    items.value = []
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
  }, 320)
}

watch([type, status], () => {
  page.value = 1
  void load()
})
watch(search, scheduleLoad)
watch(page, () => void load())
watch(size, () => {
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
        <h1 class="title">{{ t('activities.title') }}</h1>
        <p class="sub">{{ t('activities.subtitle') }}</p>
      </div>
      <span class="range num">{{ rangeLabel }}</span>
    </header>

    <div class="filters">
      <ZSegmented v-model="type" :options="typeOptions" :aria-label="t('activities.title')" />
      <ZSegmented v-model="status" :options="statusOptions" size="sm" :aria-label="t('common.all')" />
      <ZField
        v-model="search"
        class="search"
        type="search"
        icon="search"
        size="sm"
        :placeholder="t('activities.searchPlaceholder')"
      />
    </div>

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <div ref="listRef" class="list-area">
      <div
        v-if="loading && !items.length"
        class="grid"
        :class="columns === 2 ? 'cols-2' : 'cols-1'"
        :data-density="density"
      >
        <ZCard v-for="index in 6" :key="index" padding="none" class="card" :data-density="density">
          <ZSkeleton width="30%" height="0.75rem" />
          <div style="height: 1rem" />
          <ZSkeleton width="82%" height="1.4rem" />
          <div style="height: 0.75rem" />
          <ZSkeleton width="60%" height="0.8rem" />
          <div style="height: 1.4rem" />
          <ZSkeleton height="0.7rem" />
        </ZCard>
      </div>

      <div
        v-else-if="items.length"
        class="grid"
        :class="columns === 2 ? 'cols-2' : 'cols-1'"
        :data-density="density"
      >
        <RouterLink
          v-for="(activity, index) in items"
          :key="activity._id"
          :to="`/activities/${activity._id}`"
          class="card-link"
          :style="{ '--i': index }"
        >
          <ZCard hover padding="none" class="card" :data-density="density">
            <!-- 超紧凑：横向信息条，高度压到 ~72px -->
            <template v-if="density === 'ultra'">
              <div class="u-main">
                <h3 class="u-title">{{ activity.name }}</h3>
                <div class="u-meta">
                  <span class="date num">{{ formatDate(activity.date) }}</span>
                  <ZBadge tone="accent" class="u-badge">{{ t(`activities.type.${activity.type}`) }}</ZBadge>
                  <span class="u-text">{{ activity.place || '—' }}</span>
                  <span class="u-text">{{ activity.creator_name || '—' }}</span>
                </div>
              </div>
              <div class="u-side">
                <ZBadge :tone="statusTone(activity.status)">
                  {{ t(`activities.status.${activity.status}`) }}
                </ZBadge>
                <ZIcon name="arrow-up-right" :size="15" class="arrow" />
              </div>
            </template>

            <!-- 紧凑：标题+状态 / 元信息行 / 单行描述 -->
            <template v-else-if="density === 'compact'">
              <div class="c-head">
                <h3 class="card-title">{{ activity.name }}</h3>
                <ZBadge :tone="statusTone(activity.status)">
                  {{ t(`activities.status.${activity.status}`) }}
                </ZBadge>
              </div>
              <div class="card-meta">
                <span class="date num">{{ formatDate(activity.date) }}</span>
                <ZBadge tone="accent" class="meta-type">{{ t(`activities.type.${activity.type}`) }}</ZBadge>
                <span class="meta-text"><ZIcon name="pin" :size="12" />{{ activity.place || '—' }}</span>
                <span class="meta-text"><ZIcon name="user" :size="12" />{{ activity.creator_name || '—' }}</span>
              </div>
              <p v-if="activity.description" class="card-desc">{{ activity.description }}</p>
            </template>

            <!-- 舒展 / 标准：纵向卡片 -->
            <template v-else>
              <div class="card-top">
                <span class="date num">{{ formatDate(activity.date) }}</span>
                <ZBadge :tone="statusTone(activity.status)">
                  {{ t(`activities.status.${activity.status}`) }}
                </ZBadge>
              </div>
              <h2 class="card-title">{{ activity.name }}</h2>
              <p class="card-desc">{{ activity.description || '—' }}</p>
              <div class="card-meta">
                <span><ZIcon name="pin" :size="14" />{{ activity.place || '—' }}</span>
                <span><ZIcon name="user" :size="14" />{{ activity.creator_name || '—' }}</span>
              </div>
              <div class="card-foot">
                <ZBadge tone="accent">{{ t(`activities.type.${activity.type}`) }}</ZBadge>
                <ZIcon name="arrow-up-right" :size="16" class="arrow" />
              </div>
            </template>
          </ZCard>
        </RouterLink>
      </div>

      <ZEmpty v-else-if="!error" icon="compass" :title="t('activities.empty')" />
    </div>

    <AdaptivePagination
      :page="page"
      :page-size="size"
      :total="total"
      :auto="auto"
      :columns="effectiveColumns"
      :allow-auto="true"
      :allow-layout="true"
      :page-size-options="pageSizeOptions"
      :disabled="loading"
      @update:page="page = $event"
      @update:page-size="setPageSize"
      @update:auto="setAuto"
      @update:columns="setColumns"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
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
  white-space: nowrap;
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
  max-width: 360px;
  margin-left: auto;
}

.list-area {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--grid-gap, 1rem);
  transition: gap var(--dur-base) var(--ease-out);
}

.grid.cols-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

/* 密度档位：真实改变卡片内边距、间距、字号与信息排布（非 transform: scale） */
.grid[data-density='spacious'] {
  --grid-gap: 1.15rem;
  --card-pad: 1.15rem;
  --card-gap: 0.7rem;
  --title-size: 1.18rem;
  --desc-lines: 3;
  --desc-min: 3.2em;
  --meta-dir: column;
  --meta-gap: 0.3rem;
  --meta-size: var(--text-xs);
  --foot-pad: 0.7rem;
}

.grid[data-density='comfortable'] {
  --grid-gap: 1rem;
  --card-pad: 1.05rem;
  --card-gap: 0.6rem;
  --title-size: 1.05rem;
  --desc-lines: 2;
  --desc-min: 3.2em;
  --meta-dir: row;
  --meta-gap: 0.8rem;
  --meta-size: var(--text-xs);
  --foot-pad: 0.6rem;
}

.grid[data-density='compact'] {
  --grid-gap: 0.7rem;
  --card-pad: 0.8rem 0.9rem;
  --card-gap: 0.4rem;
  --title-size: 0.95rem;
  --desc-lines: 1;
  --desc-min: 0;
  --meta-dir: row;
  --meta-gap: 0.55rem;
  --meta-size: var(--text-2xs);
}

.grid[data-density='ultra'] {
  --grid-gap: 0.5rem;
  --card-pad: 0.55rem 0.8rem;
}

.card-link {
  color: inherit;
  text-decoration: none;
  animation: card-in 340ms var(--spring-soft) both;
  animation-delay: calc(min(var(--i, 0), 8) * 26ms);
}

@keyframes card-in {
  from {
    opacity: 0;
    translate: 0 10px;
  }

  to {
    opacity: 1;
    translate: 0 0;
  }
}

.card {
  display: flex;
  flex-direction: column;
  gap: var(--card-gap, 0.75rem);
  padding: var(--card-pad, 1.2rem);
  height: 100%;
  transition:
    padding var(--dur-base) var(--ease-out),
    gap var(--dur-base) var(--ease-out);
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.date {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  letter-spacing: 0.02em;
  color: var(--ink-faint);
}

.card-title {
  font-size: var(--title-size, 1.1rem);
  font-weight: 550;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 0;
}

.card-desc {
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: var(--desc-lines, 2);
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: var(--desc-min, 0);
  margin: 0;
}

.card-meta {
  display: flex;
  flex-direction: var(--meta-dir, column);
  flex-wrap: wrap;
  gap: var(--meta-gap, 0.3rem);
  font-size: var(--meta-size, var(--text-xs));
  color: var(--ink-faint);
}

.card-meta .meta-text,
.card-meta > span:not(.date) {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 紧凑模式：标题与状态同一行 */
.c-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}

.c-head .card-title {
  flex: 1;
  min-width: 0;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: var(--foot-pad, 0.75rem);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.arrow {
  color: var(--ink-faint);
  transition: transform var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.card-link:hover .arrow {
  color: var(--accent-ink);
  transform: translate(2px, -2px);
}

/* 超紧凑：横向信息条 */
.card[data-density='ultra'] {
  flex-direction: row;
  align-items: center;
  gap: 0.75rem;
  min-height: 72px;
}

.u-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.u-title {
  font-size: 0.95rem;
  font-weight: 600;
  line-height: 1.3;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.u-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.3rem 0.55rem;
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.u-text {
  max-width: 9rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.u-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.3rem;
  flex: none;
}

/* 紧凑 / 超紧凑缩小徽章，保证不挤文字 */
.card[data-density='compact'] :deep(.z-badge),
.card[data-density='ultra'] :deep(.z-badge) {
  font-size: 0.62rem;
  padding: 0.08rem 0.38rem;
}

.retry {
  margin-top: var(--space-3);
}

@media (prefers-reduced-motion: reduce) {
  .card-link {
    animation: none;
  }
  .card,
  .grid {
    transition: none;
  }
}

@media (max-width: 1080px) {
  .grid.cols-2 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .grid.cols-2 {
    grid-template-columns: 1fr;
  }

  .search {
    max-width: none;
    margin-left: 0;
    width: 100%;
  }
}
</style>
