<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { motion } from 'motion-v'
import { springs } from '@/motion/presets'
import { useNavItems } from '@/app/navigation'
import ZGlassSurface from '@/shared/ui/ZGlassSurface.vue'
import ZIcon from '@/shared/ui/ZIcon.vue'

const { t } = useI18n()
const route = useRoute()
const navItems = useNavItems()

// 移动端没有侧栏，底栏必须承载全部入口；角色多时横向滚动，不丢入口。
const items = navItems

const track = ref<HTMLElement | null>(null)
const pill = reactive({ x: 0, width: 0, height: 0, ready: false })

function measurePill() {
  const container = track.value
  if (!container) return
  const active = container.querySelector<HTMLElement>('.dock-item.is-active')
  if (!active) {
    pill.ready = false
    return
  }
  pill.x = active.offsetLeft
  pill.width = active.offsetWidth
  pill.height = active.offsetHeight
  pill.ready = true
}

let ro: ResizeObserver | null = null

onMounted(() => {
  measurePill()
  if (track.value) {
    ro = new ResizeObserver(() => measurePill())
    ro.observe(track.value)
  }
})

onBeforeUnmount(() => ro?.disconnect())

watch(
  () => route.path,
  async () => {
    await nextTick()
    measurePill()
  }
)
</script>

<template>
  <nav class="dock glass-host" aria-label="移动端主导航">
    <ZGlassSurface material="prominent" depth="lens" interactive :elasticity="0.8" />
    <div ref="track" class="dock-track">
      <motion.span
        class="dock-pill"
        aria-hidden="true"
        :initial="false"
        :animate="{
          x: pill.x,
          width: pill.width,
          height: pill.height,
          opacity: pill.ready ? 1 : 0
        }"
        :transition="springs.snappy"
      />
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
    </div>
  </nav>
</template>

<style scoped>
.dock {
  position: fixed;
  left: 50%;
  bottom: calc(10px + env(safe-area-inset-bottom, 0px));
  z-index: 40;
  display: none;
  padding: 6px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
  transform: translateX(-50%);
  max-width: calc(100vw - 20px);
  overflow: hidden;
}

.dock-track {
  position: relative;
  display: flex;
  align-items: center;
  gap: 2px;
  overflow-x: auto;
  scrollbar-width: none;
}

.dock-track::-webkit-scrollbar {
  display: none;
}

.dock-pill {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 0;
  border-radius: var(--radius-pill);
  border: 1px solid color-mix(in srgb, var(--accent) 24%, var(--glass-border));
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--glass-hi) 32%, transparent), transparent 58%),
    color-mix(in srgb, var(--accent) 16%, var(--glass-bg-strong));
  box-shadow: inset 0 1px 0 color-mix(in srgb, var(--glass-hi) 66%, transparent);
  opacity: 0;
  will-change: transform;
  pointer-events: none;
}

.dock-item {
  position: relative;
  z-index: 1;
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
  transition:
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--spring-snappy);
}

.dock-item:active {
  transform: scale(0.92);
}

.dock-item.is-active {
  color: var(--accent-ink);
}

@media (max-width: 1080px) {
  .dock {
    display: block;
  }
}
</style>
