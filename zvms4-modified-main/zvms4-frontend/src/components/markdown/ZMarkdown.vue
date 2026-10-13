<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown, renderMarkdownInline } from '@/utils/markdown.ts'

const props = defineProps<{
  source: string
  inline?: boolean
}>()

const html = computed(() =>
  props.inline ? renderMarkdownInline(props.source) : renderMarkdown(props.source)
)
</script>

<template>
  <component :is="inline ? 'span' : 'div'" v-html="html"></component>
</template>
