<script setup lang="ts">
import { ref } from 'vue'

/** 翻转卡片（规范 12）：正面图标+标题，点击翻到背面看详情。 */
const flipped = ref(false)

function toggle() {
  flipped.value = !flipped.value
}
</script>

<template>
  <div
    class="yg2-flip"
    :class="{ 'is-flipped': flipped }"
    role="button"
    tabindex="0"
    :aria-pressed="flipped"
    @click="toggle"
    @keydown.enter.prevent="toggle"
    @keydown.space.prevent="toggle"
  >
    <div class="yg2-flip__inner">
      <div class="yg2-flip__face">
        <slot name="front" />
      </div>
      <div class="yg2-flip__face yg2-flip__face--back">
        <slot name="back" />
      </div>
    </div>
  </div>
</template>
