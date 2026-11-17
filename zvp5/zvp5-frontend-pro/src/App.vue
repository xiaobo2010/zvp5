<script setup lang="ts">
import SiteNav from '@/components/SiteNav.vue'
</script>

<template>
  <div class="pro-app">
    <SiteNav />
    <main id="main">
      <RouterView />
    </main>
  </div>
</template>
