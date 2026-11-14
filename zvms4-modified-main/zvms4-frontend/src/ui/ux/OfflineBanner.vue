<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useOnline } from '@vueuse/core'

const online = useOnline()
const visible = ref(!online.value)
let timer: ReturnType<typeof setTimeout> | undefined

// 断网立刻出现；恢复后停留一小会儿再收起，避免一闪而过。
watch(online, (value) => {
  clearTimeout(timer)
  if (!value) {
    visible.value = true
    return
  }
  timer = setTimeout(() => (visible.value = false), 1200)
})

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="yg2-offline" :class="{ 'is-visible': visible }" role="status" aria-live="polite">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <path d="M12 3v9M12 17.5v.5" />
    </svg>
    <span>网络已断开，正在尝试重连…</span>
  </div>
</template>
