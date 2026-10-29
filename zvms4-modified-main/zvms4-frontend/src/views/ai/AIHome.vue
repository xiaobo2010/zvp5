<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElIcon } from 'element-plus'
import {
  AlarmClock,
  ArrowLeft,
  Box,
  Coin,
  Cpu,
  DataLine,
  Key,
  Monitor,
  Odometer,
  Tickets
} from '@element-plus/icons-vue'
import ZAiChat from '@/components/ai/ZAiChat.vue'
import ZAiQuotaPanel from '@/components/ai/ZAiQuotaPanel.vue'
import ZAiKeysAdmin from '@/components/ai/ZAiKeysAdmin.vue'
import ZAiOcrEnginesAdmin from '@/components/ai/ZAiOcrEnginesAdmin.vue'
import ZAiModelsAdmin from '@/components/ai/ZAiModelsAdmin.vue'
import ZAiQuotaAdmin from '@/components/ai/ZAiQuotaAdmin.vue'
import ZAiHealthPanel from '@/components/ai/ZAiHealthPanel.vue'
import ZAiLogsAdmin from '@/components/ai/ZAiLogsAdmin.vue'
import ZAiPromptAudit from '@/components/ai/ZAiPromptAudit.vue'
import ZAiScheduledTasks from '@/components/ai/ZAiScheduledTasks.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
}

const VALID_CURRENT = [
  'chat',
  'quota',
  'scheduled',
  'keys',
  'engines',
  'models',
  'quota-admin',
  'health',
  'logs',
  'monitor'
]
const ADMIN_ONLY = ['keys', 'engines', 'models', 'quota-admin', 'health', 'logs', 'monitor']

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
    icon: Cpu
  },
  {
    label: 'quota',
    value: 'quota',
    icon: Coin
  },
  {
    label: 'scheduled',
    value: 'scheduled',
    icon: AlarmClock
  }
])

if (isAdmin) {
  tabs.value.push({ label: 'keys', value: 'keys', icon: Key })
  tabs.value.push({ label: 'engines', value: 'engines', icon: Cpu })
  tabs.value.push({ label: 'models', value: 'models', icon: Box })
  tabs.value.push({ label: 'quota-admin', value: 'quota-admin', icon: DataLine })
  tabs.value.push({ label: 'health', value: 'health', icon: Odometer })
  tabs.value.push({ label: 'logs', value: 'logs', icon: Tickets })
  tabs.value.push({ label: 'monitor', value: 'monitor', icon: Monitor })
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
  <div class="yg-ai-home" :class="{ 'yg-ai-home--chat': current === 'chat' }">
    <ElPageHeader :icon="ArrowLeft" @back="() => router.back()" class="yg-ai-home__header">
      <template #content>
        {{ t('ai.tabs.' + current) }}
      </template>
    </ElPageHeader>

    <!-- 标签页：横向分段控件（准则 4.2.12 / 4.3.5） -->
    <div class="yg-ai-home__tabs">
      <ElSegmented v-model="current" :options="tabs">
        <template #default="props">
          <div class="flex items-center gap-2 px-2">
            <ElIcon :size="16">
              <Component :is="(props.item as SegmentedTab).icon" />
            </ElIcon>
            {{ t('ai.tabs.' + (props.item as SegmentedTab).value) }}
          </div>
        </template>
      </ElSegmented>
    </div>

    <div class="yg-ai-home__panel">
      <ZAiChat v-if="current === 'chat'" />
      <ZAiQuotaPanel v-else-if="current === 'quota'" />
      <ZAiScheduledTasks v-else-if="current === 'scheduled'" />
      <ZAiKeysAdmin v-else-if="current === 'keys'" />
      <ZAiOcrEnginesAdmin v-else-if="current === 'engines'" />
      <ZAiModelsAdmin v-else-if="current === 'models'" />
      <ZAiQuotaAdmin v-else-if="current === 'quota-admin'" />
      <ZAiHealthPanel v-else-if="current === 'health'" />
      <ZAiLogsAdmin v-else-if="current === 'logs'" />
      <ZAiPromptAudit v-else-if="current === 'monitor'" />
    </div>
  </div>
</template>

<style scoped>
.yg-ai-home {
  /* 撑满内容区：对话页要在视口内把「消息区 + 输入区」分完，
     不让输入区被长消息顶到屏幕外。
     扣掉的量 = 顶栏 + 内容区上内边距 + 内容区下内边距；
     竖屏底部有悬浮药丸导航，--yg-content-bottom-inset 由外壳给值（横屏为 0），
     所以下内边距取两者的较大值。 */
  --yg-ai-viewport-inset: calc(
    var(--yg-header-height) + env(safe-area-inset-top, 0px) + var(--yg-space-6) +
      max(var(--yg-space-6), var(--yg-content-bottom-inset, 0px))
  );

  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
  min-height: calc(100vh - var(--yg-ai-viewport-inset));
  /* 动态视口高度：移动端浏览器收起地址栏时不留多余空白 */
  min-height: calc(100dvh - var(--yg-ai-viewport-inset));
}

/*
 * 对话页：高度锁在内容区里。
 *
 * 只给 min-height 时，消息一长整页就会滚起来，输入区被顶到屏幕外；
 * 这里把高度定死、外层不滚，滚动交给消息区自己。
 *
 * max-height: 100% 以真实容器（.yg-layout__content 的内容盒）为准：
 * 离线横幅一出现，内容区就比视口矮，此时对话区必须跟着容器缩，
 * 而不是继续按视口算的高度把外层撑出滚动条。
 */
.yg-ai-home--chat {
  height: calc(100vh - var(--yg-ai-viewport-inset));
  height: calc(100dvh - var(--yg-ai-viewport-inset));
  max-height: 100%;
  min-height: 0;
  overflow: hidden;
}

/*
 * 页头与页签是固定件：不参与纵向压缩。
 *
 * 页签容器是横向滚动容器（overflow-x: auto），在 flex 里它的自动最小尺寸是 0，
 * 视口一矮就会被压成半截甚至整条消失——这里锁死它们的高度。
 * 只作用于横屏：竖屏与桌面保持原有观感。
 */
@media (orientation: landscape) and (max-height: 820px) {
  .yg-ai-home__header,
  .yg-ai-home__tabs {
    flex: none;
  }
}

.yg-ai-home__tabs {
  overflow-x: auto;
}

.yg-ai-home__panel {
  display: flex;
  flex-direction: column;
  /* 吃掉页面头部与页签剩下的高度，交给对话区自己分配 */
  flex: 1 1 auto;
  min-width: 0;
}

/* 对话页的面板不参与整页滚动：溢出只能发生在消息区里 */
.yg-ai-home--chat .yg-ai-home__panel {
  min-height: 0;
  overflow: hidden;
}

/*
 * 极矮横屏（横放手机、横屏小窗）：纵向像素是最稀缺的资源。
 * 页头与页签之间的间距先让出来，剩下的全部留给对话区。
 */
@media (orientation: landscape) and (max-height: 600px) {
  .yg-ai-home {
    gap: var(--yg-space-2);
  }
}
</style>
