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
