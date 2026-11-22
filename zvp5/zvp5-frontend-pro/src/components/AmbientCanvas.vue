<script setup lang="ts">
import { palettes, type Palette } from '@/composables/appearance'

defineProps<{ phase: Palette }>()
</script>

<template>
  <div class="ambient-canvas" :data-phase="phase" aria-hidden="true">
    <div
      v-for="palette in palettes"
      :key="palette.id"
      class="ambient-light"
      :class="`palette-${palette.id}`"
      :data-active="phase === palette.id"
    ></div>
  </div>
</template>

<style scoped>
.ambient-canvas {
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background: var(--color-bg-canvas);
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

@media (forced-colors: active) {
  .ambient-canvas {
    display: none;
  }
}
</style>
