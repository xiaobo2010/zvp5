<!-- eslint-disable vue/multi-word-component-names -->
<!-- Skeleton 是 src/ui/ux 设计基础件（骨架屏）的对外名字，单词名就是它的 API。 -->
<script setup lang="ts">
/** 骨架屏占位块：内容未到时先铺灰色块，配合扫光动画。 */
withDefaults(
  defineProps<{
    width?: string
    height?: string
    radius?: string
    /** 多行文本骨架时逐行错开宽度 */
    lines?: number
  }>(),
  { width: '100%', height: '14px', radius: 'var(--yg2-r-8)', lines: 0 }
)
</script>

<template>
  <div v-if="lines > 0" class="yg2-skeleton-lines" aria-hidden="true">
    <span
      v-for="i in lines"
      :key="i"
      class="yg2-skeleton"
      :style="{
        height,
        width: i === lines ? '62%' : width,
        borderRadius: radius
      }"
    />
  </div>
  <span
    v-else
    class="yg2-skeleton"
    aria-hidden="true"
    :style="{ height, width, borderRadius: radius }"
  />
</template>

<style scoped>
.yg2-skeleton-lines {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
