<script setup lang="ts">
import HomeView from '@/views/HomeView.vue'
</script>

<template>
  <div class="stage-app">
    <div class="stage-app__sky" aria-hidden="true"></div>
    <div class="stage-app__grid" aria-hidden="true"></div>
    <div class="stage-app__vignette" aria-hidden="true"></div>
    <HomeView />
  </div>
</template>

<style scoped>
.stage-app {
  position: relative;
  isolation: isolate;
  min-height: 100vh;
  min-height: 100dvh;
  overflow: hidden;
  background: #05070d;
}

.stage-app__sky,
.stage-app__grid,
.stage-app__vignette {
  position: fixed;
  inset: 0;
  pointer-events: none;
}

.stage-app__sky {
  background:
    radial-gradient(ellipse 60% 48% at 18% 8%, rgb(38 92 168 / 0.34), transparent 68%),
    radial-gradient(ellipse 52% 46% at 86% 12%, rgb(120 72 196 / 0.26), transparent 70%),
    radial-gradient(ellipse 68% 52% at 50% 108%, rgb(20 92 104 / 0.24), transparent 72%),
    linear-gradient(180deg, #070b14 0%, #05070d 54%, #03050a 100%);
}

.stage-app__grid {
  inset: auto 0 -18% 0;
  height: 62%;
  background-image:
    linear-gradient(rgb(118 174 255 / 0.1) 1px, transparent 1px),
    linear-gradient(90deg, rgb(118 174 255 / 0.1) 1px, transparent 1px);
  background-size: 64px 64px;
  mask-image: linear-gradient(180deg, transparent, #000 42%, #000 72%, transparent);
  transform: perspective(760px) rotateX(66deg);
  transform-origin: 50% 100%;
  opacity: 0.42;
}

.stage-app__vignette {
  background:
    radial-gradient(ellipse 88% 78% at 50% 42%, transparent 42%, rgb(2 4 9 / 0.62) 100%),
    linear-gradient(180deg, rgb(2 4 9 / 0.5), transparent 18%, transparent 82%, rgb(2 4 9 / 0.62));
}

@media (prefers-reduced-motion: reduce) {
  .stage-app__grid {
    transform: none;
    mask-image: none;
    opacity: 0.22;
  }
}
</style>
