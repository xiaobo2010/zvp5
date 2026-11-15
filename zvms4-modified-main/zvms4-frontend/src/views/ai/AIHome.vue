<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'
import ZAiChat from '@/components/ai/ZAiChat.vue'
import ZAiQuotaPanel from '@/components/ai/ZAiQuotaPanel.vue'
import ZAiKeysAdmin from '@/components/ai/ZAiKeysAdmin.vue'
import ZAiOcrEnginesAdmin from '@/components/ai/ZAiOcrEnginesAdmin.vue'
import ZAiQuotaAdmin from '@/components/ai/ZAiQuotaAdmin.vue'
import ZAiHealthPanel from '@/components/ai/ZAiHealthPanel.vue'
import ZAiLogsAdmin from '@/components/ai/ZAiLogsAdmin.vue'
import ZAiSessions from '@/components/ai/ZAiSessions.vue'
import ZAiQuotaRequests from '@/components/ai/ZAiQuotaRequests.vue'
import ZAiPrompts from '@/components/ai/ZAiPrompts.vue'

interface SegmentedTab {
  label: string
  value: string
}

const VALID_CURRENT = ['chat', 'sessions', 'quota', 'quota-requests', 'prompts', 'keys', 'engines', 'quota-admin', 'health', 'logs']
const ADMIN_ONLY = ['keys', 'engines', 'quota-admin', 'health', 'logs']

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

const isAdmin = userStore.position.includes('admin')
const canUseAi = isAdmin || userStore.position.includes('department')

if (!canUseAi) {
  router.replace('/not-found')
}

const curPage = route.params.action?.toString()

function isVisible(value: string): boolean {
  if (ADMIN_ONLY.includes(value)) {
    return isAdmin
  }
  return canUseAi
}

if (curPage && (!VALID_CURRENT.includes(curPage) || !isVisible(curPage))) {
  router.replace('/ai/chat')
}

const current = ref(
  curPage && VALID_CURRENT.includes(curPage) && isVisible(curPage) ? curPage : 'chat'
)

const segmentItems = computed(() =>
  tabs.value.map((item) => ({ value: item.value, label: t('ai.tabs.' + item.value) }))
)

const tabs = ref<SegmentedTab[]>([
  {
    label: 'chat',
    value: 'chat'
  },
  {
    label: 'sessions',
    value: 'sessions'
  },
  {
    label: 'quota',
    value: 'quota'
  },
  {
    label: 'quota-requests',
    value: 'quota-requests'
  }
])

if (isAdmin) {
  tabs.value.push({ label: 'prompts', value: 'prompts' })
  tabs.value.push({ label: 'keys', value: 'keys' })
  tabs.value.push({ label: 'engines', value: 'engines' })
  tabs.value.push({ label: 'quota-admin', value: 'quota-admin' })
  tabs.value.push({ label: 'health', value: 'health' })
  tabs.value.push({ label: 'logs', value: 'logs' })
}

watch(current, () => {
  router.replace('/ai/' + current.value)
})

watch(
  () => route.params.action,
  () => {
    const next = route.params.action?.toString()
    if (next && VALID_CURRENT.includes(next) && isVisible(next)) {
      current.value = next
    } else {
      router.replace('/ai/chat')
    }
  }
)
</script>

<template>
  <div class="yg2-page ai-page">
    <button class="ai-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">AI 网关</p>
        <h1 class="yg2-page-header__title">{{ t('ai.tabs.' + current) }}</h1>
      </div>
    </header>

    <div class="ai-tabs-scroll">
      <Segmented v-model="current" class="ai-tabs" :items="segmentItems" aria-label="AI 网关" />
    </div>

    <div class="ai-page__panel">
      <ZAiChat v-if="current === 'chat'" />
      <ZAiSessions v-else-if="current === 'sessions'" />
      <ZAiQuotaPanel v-else-if="current === 'quota'" />
      <ZAiQuotaRequests v-else-if="current === 'quota-requests'" />
      <ZAiPrompts v-else-if="current === 'prompts'" />
      <ZAiKeysAdmin v-else-if="current === 'keys'" />
      <ZAiOcrEnginesAdmin v-else-if="current === 'engines'" />
      <ZAiQuotaAdmin v-else-if="current === 'quota-admin'" />
      <ZAiHealthPanel v-else-if="current === 'health'" />
      <ZAiLogsAdmin v-else-if="current === 'logs'" />
    </div>
  </div>
</template>

<style scoped>
.ai-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100dvh - var(--yg2-topbar-h) - 96px);
}

.ai-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 12px 0 8px;
  border: 0;
  border-radius: var(--yg2-r-pill);
  background: transparent;
  color: var(--yg2-text-secondary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.ai-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.ai-tabs-scroll {
  align-self: stretch;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 4px;
  scrollbar-width: thin;
}

.ai-tabs {
  align-self: flex-start;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.ai-tabs::-webkit-scrollbar {
  display: none;
}

.ai-page__panel {
  min-width: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
}
</style>
