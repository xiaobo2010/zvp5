<script setup lang="ts">
import { onMounted } from 'vue'
import { MotionConfig } from 'motion-v'
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
  <!-- reduced-motion="user"：系统开启「减少动态效果」时，弹簧与位移收敛为淡入淡出 -->
  <MotionConfig reduced-motion="user">
    <RouterView />
    <ZGlassDefs />
    <ZToastViewport />
  </MotionConfig>
</template>

