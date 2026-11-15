<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
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

const navEl = ref<HTMLElement>()
const railThumbY = ref(0)

/** 图标栏的滑块：量出当前选中项的纵向位置，交给 CSS 做弹簧位移 */
function syncRailThumb() {
  const nav = navEl.value
  if (!nav) {
    return
  }
  const active = nav.querySelector<HTMLElement>('.yg2-nav-item.is-active')
  if (!active) {
    return
  }
  railThumbY.value = active.offsetTop + (active.offsetHeight - 48) / 2
}

watch(
  () => [route.path, props.rail, groups.value.length],
  () => {
    void nextTick(syncRailThumb)
  },
  { immediate: true }
)

onMounted(() => void nextTick(syncRailThumb))

/** 正在「弹一下」的项：用来先播完弹性动画，再切路由 */
const pressing = ref<string | null>(null)

/**
 * 点击导航时先让弹性动画跑完再跳转。
 * 之前是同步 push，页面一换，动画立刻被打断，看起来像卡顿。
 */
function go(item: NavItem) {
  const target =
    item.key === 'manage'
      ? user.position.includes('admin') || user.position.includes('department')
        ? '/manage/groups'
        : `/group/${user.class_id}/users`
      : item.path

  if (target === route.path) {
    emit('close')
    return
  }

  pressing.value = item.key
  window.setTimeout(() => {
    pressing.value = null
    void router.push(target)
    emit('close')
  }, 220)
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
        <img src="/favicon.png" alt="" width="18" height="18" />
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

    <nav ref="navEl" class="yg2-sidebar__nav">
      <span
        class="yg2-rail-thumb"
        aria-hidden="true"
        :style="{ transform: `translate3d(0, ${railThumbY}px, 0)` }"
      />
      <div v-for="group in groups" :key="group.key" class="yg2-nav-group">
        <div class="yg2-nav-group__title">{{ t(group.labelKey, group.labelKey) }}</div>
        <button
          v-for="item in group.items"
          :key="item.key"
          type="button"
          class="yg2-nav-item"
          :class="{ 'is-active': isActive(item), 'is-pressing': pressing === item.key }"
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
