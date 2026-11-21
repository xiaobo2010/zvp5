<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

function phaseAt(hour: number) {
  if (hour < 6 || hour >= 21) return 'night'
  if (hour < 10) return 'dawn'
  if (hour < 17) return 'day'
  return 'dusk'
}

const phase = ref(phaseAt(new Date().getHours()))
let timer: number | undefined

function syncLight() {
  window.clearTimeout(timer)
  if (document.hidden) return

  const now = new Date()
  phase.value = phaseAt(now.getHours())
  // Re-read local time at the hour and on resume, including after a clock or time zone change.
  const withinHour = (now.getMinutes() * 60 + now.getSeconds()) * 1_000 + now.getMilliseconds()
  timer = window.setTimeout(syncLight, 3_600_000 - withinHour)
}

onMounted(() => {
  syncLight()
  document.addEventListener('visibilitychange', syncLight)
  window.addEventListener('pageshow', syncLight)
})

onUnmounted(() => {
  window.clearTimeout(timer)
  document.removeEventListener('visibilitychange', syncLight)
  window.removeEventListener('pageshow', syncLight)
})
</script>

<template>
  <div class="ambient-canvas" :data-phase="phase" aria-hidden="true">
    <div class="ambient-light ambient-light--dawn"></div>
    <div class="ambient-light ambient-light--day"></div>
    <div class="ambient-light ambient-light--dusk"></div>
    <div class="ambient-light ambient-light--night"></div>
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
    radial-gradient(ellipse at 6% 5%, var(--light-main), transparent 62%),
    radial-gradient(ellipse at 97% 24%, var(--light-edge), transparent 56%),
    radial-gradient(ellipse at 56% 107%, var(--light-low), transparent 60%);
  transition: opacity 1.8s var(--ease-smooth);
}

.ambient-light--dawn {
  --light-main: #fbddcd;
  --light-edge: #d6e5f6;
  --light-low: #f4eed6;
}

.ambient-light--day {
  --light-main: #cfe5f8;
  --light-edge: #d8eee6;
  --light-low: #f7efdd;
}

.ambient-light--dusk {
  --light-main: #f4d7ca;
  --light-edge: #dfdcf3;
  --light-low: #efd9e4;
}

.ambient-light--night {
  --light-main: #cdd9ef;
  --light-edge: #d4e3ec;
  --light-low: #deddf2;
}

[data-phase='dawn'] .ambient-light--dawn,
[data-phase='day'] .ambient-light--day,
[data-phase='dusk'] .ambient-light--dusk,
[data-phase='night'] .ambient-light--night {
  opacity: 1;
}

@media (forced-colors: active) {
  .ambient-canvas {
    display: none;
  }
}
</style>
