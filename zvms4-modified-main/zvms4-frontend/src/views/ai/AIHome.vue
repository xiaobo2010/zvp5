<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElIcon } from 'element-plus'
import {
  ArrowLeft,
  ChatLineRound,
  Coin,
  Cpu,
  DataLine,
  Key,
  Odometer,
  Tickets
} from '@element-plus/icons-vue'
import ZAiChat from '@/components/ai/ZAiChat.vue'
import ZAiQuotaPanel from '@/components/ai/ZAiQuotaPanel.vue'
import ZAiKeysAdmin from '@/components/ai/ZAiKeysAdmin.vue'
import ZAiOcrEnginesAdmin from '@/components/ai/ZAiOcrEnginesAdmin.vue'
import ZAiQuotaAdmin from '@/components/ai/ZAiQuotaAdmin.vue'
import ZAiHealthPanel from '@/components/ai/ZAiHealthPanel.vue'
import ZAiLogsAdmin from '@/components/ai/ZAiLogsAdmin.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const VALID_CURRENT = ['chat', 'quota', 'keys', 'engines', 'quota-admin', 'health', 'logs']
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

const tabs = ref<SegmentedTab[]>([
  {
    label: 'chat',
    value: 'chat',
    icon: ChatLineRound
  },
  {
    label: 'quota',
    value: 'quota',
    icon: Coin
  }
])

if (isAdmin) {
  tabs.value.push({ label: 'keys', value: 'keys', icon: Key })
  tabs.value.push({ label: 'engines', value: 'engines', icon: Cpu })
  tabs.value.push({ label: 'quota-admin', value: 'quota-admin', icon: DataLine })
  tabs.value.push({ label: 'health', value: 'health', icon: Odometer })
  tabs.value.push({ label: 'logs', value: 'logs', icon: Tickets })
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
  <div class="px-6 py-4">
    <ElPageHeader :icon="ArrowLeft" @back="() => router.back()" class="py-4">
      <template #content>
        {{ t('ai.tabs.' + current) }}
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="flex flex-col items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              {{ t('ai.tabs.' + (props.item as SegmentedTab).value) }}
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>
    <ZAiChat v-if="current === 'chat'" />
    <ZAiQuotaPanel v-else-if="current === 'quota'" />
    <ZAiKeysAdmin v-else-if="current === 'keys'" />
    <ZAiOcrEnginesAdmin v-else-if="current === 'engines'" />
    <ZAiQuotaAdmin v-else-if="current === 'quota-admin'" />
    <ZAiHealthPanel v-else-if="current === 'health'" />
    <ZAiLogsAdmin v-else-if="current === 'logs'" />
  </div>
</template>
