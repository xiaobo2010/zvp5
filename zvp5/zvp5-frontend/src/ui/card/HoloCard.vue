<script setup lang="ts">
import YgCard from '@/ui/card/YgCard.vue'

/** 全息卡片（规范 3）：彩虹反光跟着指针移动。 */
defineProps<{ title?: string }>()
</script>

<template>
  <YgCard variant="glass" spotlight class="yg2-card--holo">
    <span class="yg2-card__holo" aria-hidden="true" />
    <span class="yg2-card__sheen" aria-hidden="true" />
    <slot />
  </YgCard>
</template>
