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
  <!-- v-html 只放在原生元素上：<component :is> 上用 v-html 会让内容被组件包裹语义干扰
       （vue/no-v-text-v-html-on-component），拆成两个原生元素后渲染结果一致。 -->
  <span v-if="inline" v-html="html" @click="onClick"></span>
  <div v-else v-html="html" @click="onClick"></div>
</template>
