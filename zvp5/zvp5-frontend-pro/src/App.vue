<script setup lang="ts">
import AmbientCanvas from '@/components/AmbientCanvas.vue'
import SiteNav from '@/components/SiteNav.vue'
import { useAmbientPalette } from '@/composables/appearance'

const palette = useAmbientPalette()
</script>

<template>
  <div class="pro-app" :class="`palette-${palette}`">
    <AmbientCanvas :phase="palette" />
    <SiteNav />
    <main id="main">
      <RouterView />
    </main>
  </div>
</template>

<style>
/* The shared Canary guide mounts under body, outside Pro's palette scope. */
dialog.zvms-theme-dialog[data-theme-app='canary-pro'] {
  --picker-hover: var(--color-bg-sunken, Canvas);
  margin: auto;
}
</style>
