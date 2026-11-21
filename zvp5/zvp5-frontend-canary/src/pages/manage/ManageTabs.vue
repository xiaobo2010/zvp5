<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'

const { t } = useI18n()
const session = useSessionStore()

const tabs = computed(() => {
  const items = [{ to: '/manage', label: t('manage.tabs.review') }]
  if (session.isManager) {
    items.push(
      { to: '/manage/users', label: t('manage.tabs.users') },
      { to: '/manage/groups', label: t('manage.tabs.groups') }
    )
  }
  return items
})
</script>

<template>
  <nav class="tabs" aria-label="管理分区">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.to"
      :to="tab.to"
      class="tab"
      exact-active-class="is-active"
    >
      {{ tab.label }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.tabs {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0.25rem;
  border-radius: 999px;
  background: color-mix(in srgb, var(--ink) 4%, transparent);
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
}

.tab {
  flex: none;
  padding: 0.45rem 0.95rem;
  border-radius: 999px;
  font-size: var(--text-sm);
  color: var(--ink-muted);
  text-decoration: none;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}

.tab:hover {
  color: var(--ink);
}

.tab.is-active {
  background: var(--surface-raised);
  color: var(--ink);
  box-shadow: var(--shadow-xs);
}
</style>
