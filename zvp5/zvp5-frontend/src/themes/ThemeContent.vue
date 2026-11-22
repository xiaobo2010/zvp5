<script setup lang="ts">
import { RouterView } from 'vue-router'

/**
 * 路由内容块：外壳只负责框架，页面内容与路由过渡在这里统一实现，
 * 三种外壳共用，避免各写一份 Transition / key 规则。
 */
defineProps<{ viewKey: string }>()
</script>

<template>
  <RouterView v-slot="{ Component }">
    <Transition name="yg-route" mode="out-in">
      <component :is="Component" :key="viewKey" class="yg-view" />
    </Transition>
  </RouterView>
</template>
