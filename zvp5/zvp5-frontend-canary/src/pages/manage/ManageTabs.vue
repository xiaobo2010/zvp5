<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@/shared/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()

const tabs = computed(() => {
  const items = [{ to: '/manage', label: t('manage.tabs.review') }]
  if (session.isManager) {
    items.push(
      { to: '/manage/users', label: t('manage.tabs.users') },
      { to: '/manage/groups', label: t('manage.tabs.groups') }
    )
  }
  if (session.isAdmin || session.can('notify.publish')) {
    items.push({ to: '/manage/notifications', label: t('manage.tabs.notify') })
  }
  if (session.isAdmin || session.can('logs.read')) {
    items.push({ to: '/manage/logs', label: t('manage.tabs.logs') })
  }
  return items
})

const root = ref<HTMLElement | null>(null)
const pill = reactive({ x: 0, y: 0, width: 0, height: 0, ready: false })

function measure() {
  const container = root.value
  if (!container) return
  const active = container.querySelector<HTMLElement>('.tab.is-active')
  if (!active) {
    pill.ready = false
    return
  }
  pill.x = active.offsetLeft
  pill.y = active.offsetTop
  pill.width = active.offsetWidth
  pill.height = active.offsetHeight
  pill.ready = true
}

let ro: ResizeObserver | null = null

onMounted(() => {
  measure()
  if (root.value) {
    ro = new ResizeObserver(() => measure())
    ro.observe(root.value)
  }
})

onBeforeUnmount(() => ro?.disconnect())

watch(
  () => [route.path, tabs.value.length],
  async () => {
    await nextTick()
    measure()
  }
)
</script>

<template>
  <nav ref="root" class="tabs" aria-label="管理分区">
    <span
      class="tab-pill"
      :class="{ 'is-ready': pill.ready }"
      aria-hidden="true"
      :style="{
        transform: `translate(${pill.x}px, ${pill.y}px)`,
        width: `${pill.width}px`,
        height: `${pill.height}px`
      }"
    />
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
  position: relative;
  isolation: isolate;
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0.25rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--line) 68%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--glass-hi) 26%, transparent), transparent),
    color-mix(in srgb, var(--surface-sunken) 56%, transparent);
  box-shadow: inset 0 1px 2px color-mix(in srgb, var(--ink) 6%, transparent);
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.tabs::-webkit-scrollbar {
  display: none;
}

.tab-pill {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 0;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--glass-border) 90%, var(--line));
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--glass-hi) 34%, transparent), transparent 58%),
    var(--surface-raised);
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    0 6px 16px -10px rgba(40, 30, 16, 0.4);
  opacity: 0;
  pointer-events: none;
  transition:
    transform 420ms var(--spring-snappy),
    width 300ms var(--spring-snappy),
    height 300ms var(--spring-snappy),
    opacity 160ms var(--ease-out);
}

.tab-pill.is-ready {
  opacity: 1;
}

.tab {
  position: relative;
  z-index: 1;
  flex: none;
  padding: 0.45rem 0.95rem;
  border-radius: 999px;
  font-size: var(--text-sm);
  color: var(--ink-muted);
  text-decoration: none;
  transition:
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-micro) var(--spring-snappy);
}

.tab:active {
  transform: scale(0.96);
}

.tab:hover {
  color: var(--ink);
}

.tab.is-active {
  color: var(--ink);
}
</style>
