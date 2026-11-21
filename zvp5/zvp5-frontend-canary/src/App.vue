<script setup lang="ts">
import { onMounted } from 'vue'
import ZToastViewport from '@/shared/ui/ZToastViewport.vue'
import ZGlassDefs from '@/shared/ui/ZGlassDefs.vue'
import { useUiStore } from '@/shared/stores/ui'

const ui = useUiStore()

onMounted(() => {
  ui.applyTheme()
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', () => {
    if (ui.themeMode === 'system') ui.applyTheme()
  })
})
</script>

<template>
  <RouterView />
  <ZGlassDefs />
  <ZToastViewport />
</template>
