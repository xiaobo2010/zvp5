<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useNavItems } from '@/app/navigation'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t } = useI18n()
const navItems = useNavItems()

// 移动端没有侧栏，底栏必须承载全部入口；角色多时横向滚动，不丢入口。
const items = navItems
</script>

<template>
  <nav class="dock glass-strong" aria-label="移动端主导航">
    <RouterLink
      v-for="item in items"
      :key="item.to"
      :to="item.to"
      class="dock-item"
      active-class="is-active"
      :exact-active-class="item.to === '/' ? 'is-active' : undefined"
    >
      <ZIcon :name="item.icon" :size="20" />
      <span>{{ t(item.labelKey) }}</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.dock {
  position: fixed;
  left: 50%;
  bottom: calc(10px + env(safe-area-inset-bottom, 0px));
  z-index: 40;
  display: none;
  align-items: center;
  gap: 2px;
  padding: 6px;
  border-radius: var(--radius-pill);
  transform: translateX(-50%);
  max-width: calc(100vw - 20px);
  overflow-x: auto;
  scrollbar-width: none;
}

.dock::-webkit-scrollbar {
  display: none;
}

.dock-item {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 58px;
  padding: 0.42rem 0.55rem;
  border-radius: var(--radius-pill);
  color: var(--ink-muted);
  font-size: 0.625rem;
  font-weight: 550;
  text-decoration: none;
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.dock-item.is-active {
  color: var(--accent-ink);
  background: color-mix(in srgb, var(--accent) 16%, transparent);
}

@media (max-width: 1080px) {
  .dock {
    display: flex;
  }
}
</style>
