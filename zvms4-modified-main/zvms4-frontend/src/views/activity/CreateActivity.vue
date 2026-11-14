<script setup lang="ts">
import { ElIcon } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { ActivityType, CreateActivityType, UserPosition } from '@/../types'
import { ArrowLeft } from '@element-plus/icons-vue'
import classifications from '@/components/tags/classifications'
import { permissions } from '@/components/activity'
import { useUserStore } from '@/stores/user'

const { t } = useI18n()
const user = useUserStore()

const route = useRoute()
const router = useRouter()

const path = ref(route.params?.type ?? '')

const tab = ref(path.value as string)

watch(
  () => route.path,
  () => {
    if (route.path === '/activity/create') {
      tab.value = ''
    }
  }
)

watch(
  () => route.params?.type,
  (type) => {
    if (typeof type === 'string') {
      tab.value = type
    }
  },
  { immediate: true }
)

const tabs = Object.entries(classifications.create).map(([key, value]) => ({
  label: '',
  value: key as CreateActivityType,
  color: value.color,
  icon: value.icon
}))

const show = ref(true)

function mov(mov: string) {
  show.value = false
  //tab.value = mov
  if (route.path === '/activity/create' && mov != '') {
    router.push(`/activity/create/${mov}`)
  } else if (mov == '') {
    router.push('/activity/create')
  } else {
    router.replace(`/activity/create/${mov}`)
  }
  show.value = true
}

watch(tab, () => {
  mov(tab.value)
})

const visibility = permissions(user.position as UserPosition[])

const visibleTabs = computed(() => tabs.filter((x) => visibility[x.value]))

</script>

<template>
  <div class="yg2-page create-page">
    <button type="button" class="create-back" @click="router.back()">
      <ElIcon><ArrowLeft /></ElIcon>
      返回
    </button>

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">{{ t('nav.create') }}</p>
        <h1 class="yg2-page-header__title">
          {{ t(tab ? `activity.create.${tab}` : 'nav.create') }}
        </h1>
      </div>
    </header>

    <nav v-if="visibleTabs.length > 1" class="yg2-segment create-tabs">
      <button
        v-for="button in visibleTabs"
        :key="button.value"
        type="button"
        class="yg2-segment__item"
        :class="{ 'is-active': button.value === tab }"
        @click="mov(button.value)"
      >
        {{ t(`activity.createMinimized.${button.value}`) }}
      </button>
    </nav>

    <RouterView v-if="show" :key="tab" />
  </div>
</template>

<style scoped>
.create-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.create-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  padding: 6px 10px 6px 6px;
  border: 0;
  border-radius: var(--yg2-r-8);
  background: transparent;
  color: var(--yg2-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: background var(--yg2-dur-1) var(--yg2-ease-out);
}

.create-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
}

.create-tabs {
  align-self: flex-start;
}
</style>
