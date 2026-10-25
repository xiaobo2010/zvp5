<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown, renderMarkdownInline, isExternalUrl } from '@/utils/markdown.ts'
import { isXuehaiAvailable } from '@/plugins/ua.ts'

const props = defineProps<{
  source: string
  inline?: boolean
}>()

// 首次渲染保持同步（挂载即有内容，避免闪烁），后续 source 变化走尾部防抖：
// 编辑器双 pane 常驻时每次按键都会换 source，逐键全量 parse + sanitize + v-html
// 替换会挤占输入帧，150ms 尾部防抖把它压到停顿后一次性完成。
const RENDER_DEBOUNCE_MS = 150

function render(source: string, inline?: boolean): string {
  return inline ? renderMarkdownInline(source) : renderMarkdown(source)
}

const html = ref(render(props.source, props.inline))

let timer: ReturnType<typeof setTimeout> | null = null

function cancelPending() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

watch(
  [() => props.source, () => props.inline],
  ([source, inline]) => {
    cancelPending()
    timer = setTimeout(() => {
      timer = null
      html.value = render(source, inline)
    }, RENDER_DEBOUNCE_MS)
  }
)

onBeforeUnmount(cancelPending)

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
