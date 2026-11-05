<script setup lang="ts">
import { ElIcon } from 'element-plus'
import { ref, watch, type Component as VueComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ZActivityList } from '@/components'
import { useHeaderStore } from '@/stores/header'
import { useI18n } from 'vue-i18n'
import { User, Group, School } from '@icon-park/vue-next'
import { useUserStore } from '@/stores/user'

const header = useHeaderStore()
const { t } = useI18n()
const user = useUserStore()

header.setHeader(t('nav.activity'))

const route = useRoute()
const router = useRouter()

const tab = ref<'campus' | 'class' | 'mine'>('mine')

watch(
  () => route.params.mode,
  (mode) => {
    const value = Array.isArray(mode) ? mode[0] : mode
    tab.value = value === 'class' || value === 'campus' ? value : 'mine'
    header.setHeader(t('nav.activity'))
  },
  { immediate: true }
)

const panes = [
  {
    value: 'mine',
    color: 'primary',
    icon: User,
    visibility: true
  },
  {
    value: 'class',
    color: 'warning',
    icon: Group,
    visibility: user.position.includes('secretary') || user.position.includes('admin')
  },
  {
    value: 'campus',
    color: 'danger',
    icon: School,
    visibility: user.position.includes('department') || user.position.includes('admin')
  }
] as Array<{
  value: string
  color: 'primary' | 'success' | 'warning' | 'danger' | 'info' | ''
  icon: VueComponent
  visibility?: boolean
}>

function moveTo(type: string) {
  // 等新路由就绪后再切换页签，避免新列表挂载时仍读到旧页签的高页码。
  router.replace({ path: `/activities/${type}`, query: {} })
}
</script>

<template>
  <div class="yg-v2-page yg-activity-page">
    <header class="yg-page-header">
      <div>
        <h1 class="yg-page-header__title">义工活动</h1>
        <p class="yg-page-header__subtitle">
          查看你参与的、班级的与全校的义工活动；状态与时长以平台记录为准。
        </p>
      </div>
    </header>

    <nav class="yg-activity-tabs" aria-label="义工活动范围">
      <button
        v-for="pane in panes.filter((item) => item.visibility)"
        :key="pane.value"
        type="button"
        class="yg-activity-tab"
        :class="{ 'is-active': tab === pane.value }"
        :aria-current="tab === pane.value ? 'page' : undefined"
        @click="moveTo(pane.value)"
      >
        <ElIcon aria-hidden="true"><component :is="pane.icon" /></ElIcon>
        <span>{{ t(`activity.view.panels.${pane.value}.short`) }}</span>
      </button>
    </nav>

    <ZActivityList class="yg-activity-list" :role="tab" :key="tab" />
  </div>
</template>
