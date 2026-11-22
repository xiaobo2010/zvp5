<script setup lang="ts">
// 顶栏不做玻璃：内容区是独立滚动容器，永远滚不到顶栏下面，
// 给它上 backdrop-filter 只是把自己背后的环境光糊成一片灰。
// 这里是实体 chrome 栏：一层顶光 + 一条发丝分隔线，材质见 v2/glass.css。
defineProps<{ title: string }>()
defineEmits<{ openDrawer: [] }>()
</script>

<template>
  <header class="yg2-topbar">
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
