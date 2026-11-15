<script setup lang="ts">
import YgCard from '@/ui/card/YgCard.vue'

/** 拉丝金属会员卡（规范 4）：斜向高光 + 压印文字。 */
</script>

<template>
  <YgCard variant="plain" :radius="22" class="yg2-card--metal">
    <div class="yg2-card__emboss">
      <slot />
    </div>
  </YgCard>
</template>
