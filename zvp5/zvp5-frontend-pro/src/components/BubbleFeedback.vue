<script setup lang="ts">
defineProps<{ as?: 'p' | 'div' }>()
</script>

<template>
  <component :is="as ?? 'p'" class="glass-feedback">
    <component :is="as === 'div' ? 'div' : 'span'" class="glass-feedback__body">
      <slot />
    </component>
  </component>
</template>
