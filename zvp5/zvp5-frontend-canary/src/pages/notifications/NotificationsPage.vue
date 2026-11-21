<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  fetchNotificationReads,
  fetchNotifications,
  markNotificationRead
} from '@/shared/api/domain'
import type { NotificationItem } from '@/types/domain'
import { ApiError } from '@/shared/http/errors'
import { formatDateTime } from '@/shared/lib/format'
import ZButton from '@/shared/ui/ZButton.vue'
import ZCard from '@/shared/ui/ZCard.vue'
import ZEmpty from '@/shared/ui/ZEmpty.vue'
import ZSkeleton from '@/shared/ui/ZSkeleton.vue'
import ZAlert from '@/shared/ui/ZAlert.vue'
import ZBadge from '@/shared/ui/ZBadge.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZMarkdown from '@/shared/ui/ZMarkdown.vue'

const { t } = useI18n()

const items = ref<NotificationItem[]>([])
const reads = ref<Record<string, number>>({})
const readsReady = ref(false)
const version = ref(0)
const loading = ref(true)
const error = ref('')
const expanded = ref<string | null>(null)

const isUnread = (item: NotificationItem) => readsReady.value && !reads.value[item.id]

const unread = computed(() => (readsReady.value ? items.value.filter(isUnread).length : 0))

async function load() {
  loading.value = true
  readsReady.value = false
  error.value = ''
  try {
    // 通知列表与已读版本是两次独立查询（ADB 单次往返可达数百毫秒），
    // 串行等待会让页面白屏双倍时间；先渲染正文，已读状态到达后再合并。
    void fetchNotificationReads()
      .then((readMap) => {
        reads.value = readMap
      })
      .catch(() => {
        reads.value = {}
      })
      .finally(() => {
        readsReady.value = true
      })

    const list = await fetchNotifications()
    items.value = list.notifications
    version.value = list.version
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : t('common.error')
  } finally {
    loading.value = false
  }
}

async function open(item: NotificationItem) {
  if (expanded.value === item.id) {
    expanded.value = null
    return
  }
  expanded.value = item.id
  if (!reads.value[item.id]) {
    try {
      await markNotificationRead(item.id)
      reads.value = { ...reads.value, [item.id]: version.value }
    } catch {
      // 已读失败不阻断阅读；下次进入会重新拉取。
    }
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1 class="title">{{ t('notifications.title') }}</h1>
        <p class="sub">{{ t('notifications.subtitle') }}</p>
      </div>
      <span v-if="unread" class="unread num">{{ unread }} {{ t('notifications.unread') }}</span>
    </header>

    <ZAlert v-if="error" tone="danger" :title="t('common.error')" :description="error">
      <div class="retry">
        <ZButton size="sm" icon="refresh" @click="load">{{ t('common.retry') }}</ZButton>
      </div>
    </ZAlert>

    <div v-if="loading" class="list">
      <ZCard v-for="index in 4" :key="index">
        <ZSkeleton width="46%" height="1rem" />
        <div style="height: 0.6rem" />
        <ZSkeleton width="24%" height="0.7rem" />
      </ZCard>
    </div>

    <div v-else-if="items.length" class="list">
      <ZCard
        v-for="item in items"
        :key="item.id"
        padding="none"
        class="notice"
        :class="{ 'is-unread': isUnread(item), 'is-open': expanded === item.id }"
      >
        <button type="button" class="notice-head" @click="open(item)">
          <span class="dot" :class="{ unread: isUnread(item) }" aria-hidden="true" />
          <span class="notice-title">
            <strong>{{ item.title }}</strong>
            <small>
              {{ item.author_name || '—' }}
              <template v-if="item.updated_at"> · {{ formatDateTime(item.updated_at) }}</template>
            </small>
          </span>
          <ZBadge v-if="isUnread(item)" tone="accent" size="sm">{{ t('notifications.unread') }}</ZBadge>
          <ZIcon
            name="chevron-down"
            :size="17"
            class="chevron"
            :class="{ rotated: expanded === item.id }"
          />
        </button>
        <Transition name="expand">
          <div v-if="expanded === item.id" class="notice-body">
            <ZMarkdown :source="item.content" />
            <p class="version-tag">v{{ item.version }}</p>
          </div>
        </Transition>
      </ZCard>
    </div>

    <ZEmpty v-else-if="!error" icon="bell" :title="t('notifications.empty')" />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 880px;
  margin-inline: auto;
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

.unread {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--accent-ink);
  padding: 0.25rem 0.6rem;
  border-radius: var(--radius-pill);
  background: var(--accent-soft);
  white-space: nowrap;
}

.list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.notice {
  overflow: hidden;
  transition: border-color var(--dur-fast) var(--ease-out);
}

.notice.is-unread {
  border-color: color-mix(in srgb, var(--accent) 32%, var(--line));
}

.notice-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-4) var(--space-5);
  text-align: left;
}

.dot {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: color-mix(in srgb, var(--ink) 14%, transparent);
}

.dot.unread {
  background: var(--accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 16%, transparent);
}

.notice-title {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  flex: 1;
  min-width: 0;
}

.notice-title strong {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notice-title small {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.chevron {
  flex: none;
  color: var(--ink-faint);
  transition: transform var(--dur-fast) var(--ease-out);
}

.chevron.rotated {
  transform: rotate(180deg);
}

.notice-body {
  padding: 0 var(--space-5) var(--space-5);
  border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  padding-top: var(--space-4);
}

.version-tag {
  margin-top: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.expand-enter-active {
  transition:
    opacity 200ms var(--ease-out),
    transform 380ms var(--spring-soft);
}

.expand-leave-active {
  transition:
    opacity 150ms var(--ease-out),
    transform 150ms var(--ease-out);
}

.expand-enter-from {
  opacity: 0;
  transform: translateY(-6px) scale(0.995);
}

.expand-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.retry {
  margin-top: var(--space-3);
}

@media (max-width: 560px) {
  .notice-head {
    padding-inline: var(--space-4);
  }

  .notice-body {
    padding-inline: var(--space-4);
  }
}
</style>
