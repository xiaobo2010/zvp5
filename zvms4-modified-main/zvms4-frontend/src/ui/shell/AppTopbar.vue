<script setup lang="ts">
defineProps<{ title: string }>()
defineEmits<{ openDrawer: [] }>()
</script>

<template>
  <header class="yg2-topbar">
    <div class="yg2-lens-clip" aria-hidden="true">
      <span class="yg2-lens-clip__blob yg2-lens-clip__blob--2" />
      <span class="yg2-lens-clip__blob yg2-lens-clip__blob--3" />
    </div>
    <button
      type="button"
      class="yg2-topbar__menu"
      aria-label="打开导航"
      @click="$emit('openDrawer')"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
        <path d="M4 7h16M4 12h16M4 17h16" />
      </svg>
    </button>
    <div class="yg2-topbar__title">{{ title }}</div>
    <div class="yg2-topbar__actions">
      <slot name="actions" />
    </div>
  </header>
</template>
