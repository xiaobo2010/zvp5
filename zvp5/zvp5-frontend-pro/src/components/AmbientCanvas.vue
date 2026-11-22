<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { palettes, type Palette } from '@/composables/appearance'

defineProps<{ phase: Palette }>()

const paused = ref(false)

function syncVisibility() {
  paused.value = document.hidden
}

onMounted(() => {
  syncVisibility()
  document.addEventListener('visibilitychange', syncVisibility)
})

onUnmounted(() => document.removeEventListener('visibilitychange', syncVisibility))
</script>

<template>
  <div class="ambient-canvas" :data-phase="phase" aria-hidden="true">
    <div class="ambient-flow" :class="{ 'is-paused': paused }">
      <div
        v-for="palette in palettes"
        :key="palette.id"
        class="ambient-light"
        :class="`palette-${palette.id}`"
        :data-active="phase === palette.id"
      ></div>
    </div>
  </div>
</template>

<style scoped>
.ambient-canvas {
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  overflow: hidden;
  background: var(--color-bg-canvas);
}

.ambient-flow {
  position: absolute;
  inset: -8%;
  animation: ambient-drift 44s ease-in-out infinite alternate;
}

.ambient-flow.is-paused { animation-play-state: paused; }

@keyframes ambient-drift {
  from { transform: translate(-3%, -2%) scale(1); }
  52% { transform: translate(2%, 3%) scale(1.045); }
  to { transform: translate(4%, -3%) scale(1.015); }
}

.ambient-light {
  position: absolute;
  inset: 0;
  opacity: 0;
  background:
    radial-gradient(ellipse at 6% 5%, var(--palette-main), transparent 62%),
    radial-gradient(ellipse at 97% 24%, var(--palette-edge), transparent 56%),
    radial-gradient(ellipse at 56% 107%, var(--palette-low), transparent 60%);
  transition: opacity 1.8s var(--ease-smooth);
}

.ambient-light[data-active='true'] {
  opacity: 1;
}

@media (prefers-reduced-motion: reduce), (prefers-contrast: more) {
  .ambient-flow { animation: none; }
}

@media (forced-colors: active) {
  .ambient-canvas {
    display: none;
  }
}
</style>
