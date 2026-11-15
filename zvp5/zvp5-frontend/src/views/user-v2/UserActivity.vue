<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ZActivityList } from '@/components'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'
import { useUserStore } from '@/stores/user'

const header = useHeaderStore()
const { t } = useI18n()
const user = useUserStore()

header.setHeader(t('nav.activity'))

const route = useRoute()
const router = useRouter()

const tab = ref('mine')
// 角色类型断言放在脚本里：模板内联 TS 联合会被 vue/no-deprecated-filter 误判成过滤器
const role = computed(() => tab.value as 'campus' | 'class' | 'mine')

watch(
  () => route.params?.type,
  () => {
    tab.value = route.path.startsWith('/activities/')
      ? route.path.replace('/activities/', '') || 'mine'
      : 'mine'
    header.setHeader(t('nav.activity'))
  },
  { immediate: true }
)

const panes = [
  {
    value: 'mine',
    visibility: true
  },
  {
    value: 'class',
    visibility: user.position.includes('secretary') || user.position.includes('admin')
  },
  {
    value: 'campus',
    visibility: user.position.includes('department') || user.position.includes('admin')
  }
].filter((pane) => pane.visibility)

const segmentItems = computed(() =>
  panes.map((pane) => ({
    value: pane.value,
    label: t(`activity.view.panels.${pane.value}.short`)
  }))
)

function moveTo(type: string) {
  // 等新路由就绪后再切换页签，避免新列表挂载时仍读到旧页签的高页码。
  router.replace({ path: `/activities/${type}`, query: {} })
}
</script>

<template>
  <div class="yg2-page">
    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">志愿项目</p>
        <h1 class="yg2-page-header__title">
          {{ t(`activity.view.panels.${tab || 'mine'}.name`) }}
        </h1>
      </div>
      <div v-if="panes.length > 1" class="yg2-page-header__actions">
        <Segmented
          :model-value="tab"
          :items="segmentItems"
          aria-label="项目范围"
          @update:model-value="moveTo"
        />
      </div>
    </header>

    <ZActivityList embed :role="role" :key="tab" />
  </div>
</template>
