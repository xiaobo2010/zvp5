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

const { t } = useI18n()

const type = ref('all')
const status = ref('all')
const search = ref('')
const page = ref(1)
const size = 12
const total = ref(0)
const items = ref<Activity[]>([])
const loading = ref(true)
const error = ref('')

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

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size)))
const rangeLabel = computed(() => {
  if (!total.value) return '0'
  const start = (page.value - 1) * size + 1
  const end = Math.min(total.value, page.value * size)
  return `${start}–${end} / ${total.value}`
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchActivities({
      page: page.value,
      size,
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

    <div v-if="loading" class="grid">
      <ZCard v-for="index in 6" :key="index" class="card">
        <ZSkeleton width="30%" height="0.75rem" />
        <div style="height: 1rem" />
        <ZSkeleton width="82%" height="1.4rem" />
        <div style="height: 0.75rem" />
        <ZSkeleton width="60%" height="0.8rem" />
        <div style="height: 1.4rem" />
        <ZSkeleton height="0.7rem" />
      </ZCard>
    </div>

    <div v-else-if="items.length" class="grid">
      <RouterLink
        v-for="activity in items"
        :key="activity._id"
        :to="`/activities/${activity._id}`"
        class="card-link"
      >
        <ZCard hover class="card">
          <div class="card-top">
            <span class="date num">{{ formatDate(activity.date) }}</span>
            <ZBadge :tone="activity.status === 'effective' ? 'success' : activity.status === 'pending' ? 'warning' : 'danger'">
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
        </ZCard>
      </RouterLink>
    </div>

    <ZEmpty v-else-if="!error" icon="compass" :title="t('activities.empty')" />

    <nav v-if="totalPages > 1" class="pager" aria-label="分页">
      <ZButton
        variant="ghost"
        size="sm"
        icon="chevron-left"
        :disabled="page <= 1 || loading"
        @click="page -= 1"
      >
        {{ t('common.back') }}
      </ZButton>
      <span class="page-num num">{{ page }} / {{ totalPages }}</span>
      <ZButton
        variant="ghost"
        size="sm"
        icon-right="chevron-right"
        :disabled="page >= totalPages || loading"
        @click="page += 1"
      >
        {{ t('home.viewAll') }}
      </ZButton>
    </nav>
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

.grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

.card-link {
  color: inherit;
  text-decoration: none;
}

.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  height: 100%;
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
  font-size: var(--text-lg);
  font-weight: 550;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-desc {
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.9em;
}

.card-meta {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.card-meta span {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: var(--space-3);
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

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  padding-top: var(--space-2);
}

.page-num {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.retry {
  margin-top: var(--space-3);
}

@media (max-width: 1080px) {
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 680px) {
  .grid {
    grid-template-columns: 1fr;
  }

  .search {
    max-width: none;
    margin-left: 0;
    width: 100%;
  }
}
</style>
