<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { NAV_GROUPS, FOOT_ITEMS, type NavItem } from './nav'

const props = defineProps<{ open: boolean; rail?: boolean }>()
const emit = defineEmits<{ toggleRail: []; close: [] }>()

const route = useRoute()
const router = useRouter()
const user = useUserStore()
const { t } = useI18n({ useScope: 'global' })

const groups = computed(() =>
  NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.show(user.position))
  })).filter((group) => group.items.length > 0)
)

const footItems = computed(() => FOOT_ITEMS.filter((item) => item.show(user.position)))

function isActive(item: NavItem) {
  return item.judge(route.path)
}

function go(item: NavItem) {
  if (item.key === 'manage') {
    const staff = user.position.includes('admin') || user.position.includes('department')
    router.push(staff ? '/manage/groups' : `/group/${user.class_id}/users`)
  } else {
    router.push(item.path)
  }
  emit('close')
}
</script>

<template>
  <aside class="yg2-sidebar" :class="{ 'is-open': props.open }" aria-label="主导航">
    <div class="yg2-lens-clip" aria-hidden="true">
      <span class="yg2-lens-clip__blob yg2-lens-clip__blob--1" />
      <span class="yg2-lens-clip__blob yg2-lens-clip__blob--2" />
      <span class="yg2-lens-clip__blob yg2-lens-clip__blob--3" />
    </div>
    <div class="yg2-sidebar__brand">
      <span class="yg2-sidebar__logo" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 21s-7-4.6-7-10a4.5 4.5 0 0 1 7.7-3.2L12 8.6l-.7-.8A4.5 4.5 0 0 1 19 11c0 5.4-7 10-7 10Z" />
        </svg>
      </span>
      <span class="yg2-sidebar__title">义工平台</span>
      <button
        type="button"
        class="yg2-sidebar__collapse"
        :title="
          props.rail ? t('nav.expandSidebar', '展开侧边栏') : t('nav.toggleSidebar', '收起侧边栏')
        "
        :aria-label="
          props.rail ? t('nav.expandSidebar', '展开侧边栏') : t('nav.toggleSidebar', '收起侧边栏')
        "
        @click="emit('toggleRail')"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path :d="props.rail ? 'm10 6 6 6-6 6' : 'm14 6-6 6 6 6'" />
        </svg>
      </button>
    </div>

    <nav class="yg2-sidebar__nav">
      <div v-for="group in groups" :key="group.key" class="yg2-nav-group">
        <div class="yg2-nav-group__title">{{ t(group.labelKey, group.labelKey) }}</div>
        <button
          v-for="item in group.items"
          :key="item.key"
          type="button"
          class="yg2-nav-item"
          :class="{ 'is-active': isActive(item) }"
          @click="go(item)"
        >
          <span class="yg2-nav-item__icon"><component :is="item.icon" /></span>
          <span class="yg2-nav-item__label">{{ t(`nav.${item.key}`, item.key) }}</span>
        </button>
      </div>
    </nav>

    <div class="yg2-sidebar__foot">
      <button
        v-for="item in footItems"
        :key="item.key"
        type="button"
        class="yg2-nav-item"
        :class="{ 'is-active': isActive(item) }"
        @click="go(item)"
      >
        <span class="yg2-nav-item__icon"><component :is="item.icon" /></span>
        <span class="yg2-nav-item__label">{{ t(`nav.${item.key}`, item.key) }}</span>
      </button>
    </div>
  </aside>
</template>
