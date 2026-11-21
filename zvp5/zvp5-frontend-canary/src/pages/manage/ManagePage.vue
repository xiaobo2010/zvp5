<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchActivities } from '@/shared/api/domain'
import { updateActivityStatus } from '@/shared/api/manage'
import type { Activity } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDate } from '@/shared/lib/format'
import { useSessionStore } from '@/shared/stores/session'
import { useUiStore } from '@/shared/stores/ui'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZField from '@/shared/ui/ZField.vue'
import ZSegmented from '@/shared/ui/ZSegmented.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ManageTabs from './ManageTabs.vue'

const { t } = useI18n()
const session = useSessionStore()
const ui = useUiStore()

type Tab = 'queue' | 'all'

const tab = ref<Tab>('queue')
const status = ref('all')
const search = ref('')
const page = ref(1)
const size = 20
const total = ref(0)
const items = ref<Activity[]>([])
const loading = ref(true)
const error = ref('')
const acting = ref<string | null>(null)

const canReview = computed(() => session.isManager)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size)))

const tabOptions = computed(() => [
  { value: 'queue', label: t('manage.queue') },
  { value: 'all', label: t('manage.all') }
])

const statusOptions = computed(() => [
  { value: 'all', label: t('common.all') },
  { value: 'effective', label: t('activities.status.effective') },
  { value: 'pending', label: t('activities.status.pending') },
  { value: 'refused', label: t('activities.status.refused') }
])

const queryStatus = computed(() => {
  if (tab.value === 'queue') return 'pending'
  return status.value === 'all' ? '' : status.value
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await fetchActivities({
      page: page.value,
      size,
      query: search.value.trim(),
      type: '',
      status: queryStatus.value,
      sort: 'date',
      asc: false
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

async function review(activity: Activity, next: 'effective' | 'refused') {
  if (acting.value) return
  acting.value = activity._id
  try {
    await updateActivityStatus(activity._id, next)
    ui.notify(t('manage.done'), 'success')
    if (tab.value === 'queue') {
      items.value = items.value.filter((item) => item._id !== activity._id)
      total.value = Math.max(0, total.value - 1)
      if (!items.value.length) await load()
    } else {
      activity.status = next
    }
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) {
      ui.notify(t('manage.conflict'), 'neutral')
      await load()
    } else if (cause instanceof ApiError && cause.status === 403) {
      ui.notify(t('manage.reviewerOnly'), 'danger')
    } else {
      ui.notify(cause instanceof ApiError ? cause.message : t('common.error'), 'danger')
    }
  } finally {
    acting.value = null
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

watch(tab, () => {
  page.value = 1
  void load()
})
watch(status, () => {
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
        <h1 class="title">{{ t('manage.title') }}</h1>
        <p class="sub">{{ t('manage.subtitle') }}</p>
      </div>
      <span class="range num">{{ total }}</span>
    </header>

    <ManageTabs />

    <ZAlert v-if="!canReview" tone="warning" :description="t('manage.noPermission')" />

    <div class="filters">
      <ZSegmented v-model="tab" :options="tabOptions" :aria-label="t('manage.title')" />
      <ZSegmented
        v-if="tab === 'all'"
        v-model="status"
        :options="statusOptions"
        size="sm"
        :aria-label="t('common.all')"
      />
      <ZField
        v-model="search"
        class="search"
        type="search"
        icon="search"
        size="sm"
        :placeholder="t('manage.searchPlaceholder')"
      />
    </div>

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <ZCard padding="none">
      <div v-if="loading" class="rows">
        <div v-for="index in 5" :key="index" class="row">
          <ZSkeleton width="4.2rem" height="0.8rem" />
          <ZSkeleton width="42%" height="0.95rem" />
          <ZSkeleton width="6rem" height="0.8rem" />
        </div>
      </div>

      <ul v-else-if="items.length" class="rows">
        <li v-for="activity in items" :key="activity._id" class="row">
          <span class="date num">{{ formatDate(activity.date).slice(5) }}</span>

          <RouterLink :to="`/activities/${activity._id}`" class="main">
            <strong>{{ activity.name }}</strong>
            <small>
              {{ t(`activities.type.${activity.type}`) }}
              <template v-if="activity.creator_name"> · {{ activity.creator_name }}</template>
            </small>
          </RouterLink>

          <ZBadge
            :tone="activity.status === 'effective' ? 'success' : activity.status === 'pending' ? 'warning' : 'danger'"
          >
            {{ t(`activities.status.${activity.status}`) }}
          </ZBadge>

          <div v-if="canReview && activity.status === 'pending'" class="actions">
            <ZButton
              size="sm"
              variant="secondary"
              :disabled="acting === activity._id"
              @click="review(activity, 'refused')"
            >
              {{ t('manage.refuse') }}
            </ZButton>
            <ZButton
              size="sm"
              variant="primary"
              :disabled="acting === activity._id"
              @click="review(activity, 'effective')"
            >
              {{ t('manage.approve') }}
            </ZButton>
          </div>

          <RouterLink v-else :to="`/activities/${activity._id}`" class="open" :aria-label="t('manage.open')">
            <ZIcon name="arrow-up-right" :size="16" />
          </RouterLink>
        </li>
      </ul>

      <ZEmpty
        v-else-if="!error"
        compact
        icon="check"
        :title="tab === 'queue' ? t('manage.emptyQueue') : t('manage.emptyList')"
      />
    </ZCard>

    <p class="hint">{{ t('manage.memberHint') }}</p>

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
  max-width: 340px;
  margin-left: auto;
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
}

.row:last-child {
  border-bottom: 0;
}

.date {
  flex: none;
  width: 3.1rem;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.main {
  display: flex;
  flex-direction: column;
  gap: 0.12rem;
  flex: 1;
  min-width: 0;
  color: inherit;
  text-decoration: none;
}

.main strong {
  font-size: var(--text-sm);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: none;
}

.open {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 50%;
  color: var(--ink-faint);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.open:hover {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
  color: var(--accent-ink);
}

.hint {
  font-size: var(--text-xs);
  color: var(--ink-faint);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
}

.page-num {
  font-size: var(--text-xs);
  color: var(--ink-muted);
}

.retry {
  margin-top: var(--space-3);
}

@media (max-width: 680px) {
  .row {
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-3);
  }

  .main {
    flex-basis: calc(100% - 5rem);
  }

  .actions {
    margin-left: auto;
  }

  .search {
    max-width: none;
    margin-left: 0;
    width: 100%;
  }
}
</style>
