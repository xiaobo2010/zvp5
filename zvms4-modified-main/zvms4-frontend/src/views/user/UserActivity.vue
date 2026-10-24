<script setup lang="ts">
import { ElSpace, ElButton } from 'element-plus'
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
  <div class="p-4" style="width: 100%; height: 100%">
    <div class="flex px-12 py-4">
      <span class="text-xl">
        {{ t(`activity.view.panels.${tab ? tab : 'mine'}.name`) }}
      </span>
      <div class="flex justify-end" style="flex-grow: 1;">
        <ElSpace v-if="panes.length > 1">
          <ElButton
            text
            bg
            size="small"
            v-for="pane in panes.filter((x) => x.visibility)"
            :key="pane.value"
            :type="pane.color"
            :icon="pane.icon"
            @click="moveTo(pane.value)"
            :disabled="tab === pane.value || (tab === '' && pane.value === 'mine')"
          >
            {{ t(`activity.view.panels.${pane.value}.short`) }}
          </ElButton>
        </ElSpace>
      </div>
    </div>
    <ZActivityList class="mx-12" :role="tab" :key="tab" />
  </div>
</template>
