<script setup lang="ts">
/**
 * 空状态 / 错误状态 / 结果状态的统一壳子：
 * 插图 + 标题 + 说明 + 一到两个下一步动作。
 */
withDefaults(
  defineProps<{
    tone?: 'neutral' | 'error' | 'success'
    title?: string
    description?: string
    /** 给插图用的 emoji 或字符，避免引入图片资源 */
    glyph?: string
  }>(),
  { tone: 'neutral', title: '', description: '', glyph: '' }
)
</script>

<template>
  <div class="yg2-state">
    <div
      class="yg2-state__art"
      :class="{
        'yg2-state__art--error': tone === 'error',
        'yg2-state__art--success': tone === 'success'
      }"
      aria-hidden="true"
    >
      <slot name="art">
        <svg
          v-if="tone === 'success'"
          width="30"
          height="30"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="m4 12.5 5 5L20 6.5" />
        </svg>
        <svg
          v-else-if="tone === 'error'"
          width="30"
          height="30"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7.5v5.5M12 16.4v.2" />
        </svg>
        <svg
          v-else
          width="30"
          height="30"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z" />
          <path d="M4 7.5 12 12l8-4.5M12 12v9" />
        </svg>
      </slot>
    </div>
    <p v-if="title" class="yg2-state__title">{{ title }}</p>
    <p v-if="description" class="yg2-state__desc">{{ description }}</p>
    <div v-if="$slots.actions" class="yg2-state__actions">
      <slot name="actions" />
    </div>
  </div>
</template>
