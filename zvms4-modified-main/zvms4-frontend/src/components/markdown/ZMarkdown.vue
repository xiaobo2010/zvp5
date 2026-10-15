<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown, renderMarkdownInline, isExternalUrl } from '@/utils/markdown.ts'
import { isXuehaiAvailable } from '@/plugins/ua.ts'

const props = defineProps<{
  source: string
  inline?: boolean
}>()

const html = computed(() =>
  props.inline ? renderMarkdownInline(props.source) : renderMarkdown(props.source)
)

function onClick(event: MouseEvent) {
  if (!isXuehaiAvailable()) return
  const anchor = (event.target as Element | null)?.closest('a[href]')
  if (!anchor) return
  const href = anchor.getAttribute('href')
  if (!href || !isExternalUrl(href)) return
  event.preventDefault()
  ElMessage({
    message: '学海设备不支持跳转外部链接',
    type: 'warning',
    grouping: true
  })
}
</script>

<template>
  <component :is="inline ? 'span' : 'div'" v-html="html" @click="onClick"></component>
</template>
