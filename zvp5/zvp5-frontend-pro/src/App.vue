<script setup lang="ts">
import AmbientCanvas from '@/components/AmbientCanvas.vue'
import SiteNav from '@/components/SiteNav.vue'
</script>

<template>
  <div class="pro-app">
    <AmbientCanvas />
    <SiteNav />
    <main id="main">
      <RouterView />
    </main>
  </div>
</template>
