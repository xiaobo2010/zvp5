<script setup lang="ts">
import { ref, watch } from 'vue'

/** 模糊渐显：先铺占位色，图片 onload 后从 blur 淡入。 */
const props = withDefaults(
  defineProps<{ src: string; alt?: string; ratio?: string }>(),
  { alt: '', ratio: '16 / 9' }
)

const loaded = ref(false)
watch(
  () => props.src,
  () => (loaded.value = false)
)
</script>

<template>
  <div class="yg2-image" :style="{ aspectRatio: ratio }">
    <img
      class="yg2-image__img"
      :class="{ 'is-loaded': loaded }"
      :src="src"
      :alt="alt"
      loading="lazy"
      decoding="async"
      @load="loaded = true"
    />
  </div>
</template>
