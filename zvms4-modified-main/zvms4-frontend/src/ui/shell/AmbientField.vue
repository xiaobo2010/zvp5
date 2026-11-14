<script setup lang="ts">
/**
 * 环境光场：背后缓慢漂移的低饱和光斑，只负责氛围，不参与交互。
 * tone 由外壳按板块传入（activities / groups / ai / manage），换色相不换结构。
 */
withDefaults(defineProps<{ tone?: string }>(), { tone: 'activities' })
</script>

<template>
  <div class="yg2-ambient" :data-ambient="tone" aria-hidden="true">
    <div class="yg2-ambient__blob" />
    <div class="yg2-ambient__blob" />
    <div class="yg2-ambient__blob" />
  </div>
</template>
