<script setup lang="ts">
withDefaults(
  defineProps<{
    width?: string
    height?: string
    radius?: string
    circle?: boolean
  }>(),
  { width: '100%', height: '1rem', radius: 'var(--radius-sm)', circle: false }
)
</script>

<template>
  <span
    class="z-skeleton"
    :style="{
      width,
      height,
      borderRadius: circle ? '50%' : radius,
      display: circle ? 'inline-block' : 'block'
    }"
    aria-hidden="true"
  />
</template>

<style scoped>
.z-skeleton {
  background: linear-gradient(
    100deg,
    var(--surface-sunken) 20%,
    color-mix(in srgb, var(--surface-raised) 78%, var(--surface-sunken)) 42%,
    var(--surface-sunken) 64%
  );
  background-size: 220% 100%;
  animation: skeleton-shimmer 1.7s var(--ease-in-out) infinite;
}

@keyframes skeleton-shimmer {
  0% {
    background-position: 120% 0;
  }
  100% {
    background-position: -120% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .z-skeleton {
    animation: none;
  }
}
</style>
