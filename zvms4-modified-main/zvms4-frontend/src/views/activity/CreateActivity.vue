<script setup lang="ts">
import { ElIcon } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'
import type { ActivityType, CreateActivityType, UserPosition } from '@/../types'
import { ArrowLeft } from '@element-plus/icons-vue'
import classifications from '@/components/tags/classifications'
import { permissions } from '@/components/activity'
import { useUserStore } from '@/stores/user'
import GlassBackButton from '@/ui/glass/GlassBackButton.vue'

const { t } = useI18n()
const user = useUserStore()

const route = useRoute()
const router = useRouter()

const path = ref(route.params?.type ?? '')

const tab = ref(path.value as string)

const segmentItems = computed(() =>
  visibleTabs.value.map((item) => ({
    value: item.value,
    label: t(`activity.createMinimized.${item.value}`)
  }))
)

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
    <GlassBackButton class="create-back" @click="router.back()" />

    <header class="yg2-page-header">
      <div>
        <p class="yg2-page-header__eyebrow">{{ t('nav.create') }}</p>
        <h1 class="yg2-page-header__title">
          {{ t(tab ? `activity.create.${tab}` : 'nav.create') }}
        </h1>
      </div>
    </header>

    <Segmented
      v-if="visibleTabs.length > 1"
      :model-value="tab"
      class="create-tabs"
      :items="segmentItems"
      aria-label="创建方式"
      @update:model-value="mov"
    />

    <RouterView v-if="show" :key="tab" />
  </div>
</template>

<style scoped>
.create-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}



.create-tabs {
  align-self: flex-start;
}

.create-back {
  align-self: flex-start;
}
</style>
