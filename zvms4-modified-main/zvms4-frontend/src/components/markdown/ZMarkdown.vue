<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  MARKDOWN_MAX_CHARS,
  isExternalUrl,
  renderMarkdown,
  renderMarkdownInline
} from '@/utils/markdown.ts'
import { isXuehaiAvailable } from '@/plugins/ua.ts'

const props = withDefaults(
  defineProps<{
    source: string
    /** 行内模式：渲染成 span 而不是 div。 */
    inline?: boolean
    /** 渲染上限（字符）：超过只渲染前半截并给一句提示。≤0 表示不限制。 */
    maxChars?: number
    /** 立刻渲染：调用方自己已经节流过时用，跳过组件内的防抖。 */
    immediate?: boolean
  }>(),
  {
    inline: false,
    maxChars: MARKDOWN_MAX_CHARS,
    immediate: false
  }
)

// 首次渲染保持同步（挂载即有内容，避免闪烁），后续 source 变化走尾部防抖：
// 编辑器双 pane 常驻时每次按键都会换 source，逐键全量 parse + sanitize + v-html
// 替换会挤占输入帧，150ms 尾部防抖把它压到停顿后一次性完成。
const RENDER_DEBOUNCE_MS = 150

/** 渲染缓存条数：同一段文字再回来时直接复用 html，不重新 parse + sanitize。 */
const CACHE_LIMIT = 8

/** 截断提示：接在正文后面（我们自己拼的静态标记，不经过净化）。 */
const TRUNCATION_NOTICE = '<span class="z-markdown__truncated">内容过长已截断</span>'

const html = ref('')
const truncated = ref(false)

/** 渲染缓存：key = 行内模式 + 源文本。 */
const cache = new Map<string, string>()

function cacheKey(source: string, inline: boolean): string {
  return `${inline ? 'i' : 'b'} ${source}`
}

function renderCached(source: string, inline: boolean): string {
  const key = cacheKey(source, inline)
  const hit = cache.get(key)
  if (hit !== undefined) {
    // 命中就挪到队尾：最近用过的先被淘汰。
    cache.delete(key)
    cache.set(key, hit)
    return hit
  }
  const rendered = inline ? renderMarkdownInline(source) : renderMarkdown(source)
  cache.set(key, rendered)
  if (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) {
      cache.delete(oldest)
    }
  }
  return rendered
}

/**
 * 超长文本只渲染前半截。
 *
 * 切在行尾：半行 Markdown（半个表格行、半个代码块）渲染出来是乱的，宁可少给一行。
 */
function cutSource(source: string, maxChars: number): { text: string; cut: boolean } {
  if (maxChars <= 0 || source.length <= maxChars) {
    return { text: source, cut: false }
  }
  const head = source.slice(0, maxChars)
  const lastBreak = head.lastIndexOf('\n')
  return { text: lastBreak > 0 ? head.slice(0, lastBreak) : head, cut: true }
}

function apply(source: string, inline: boolean, maxChars: number): void {
  const { text, cut } = cutSource(source, maxChars)
  const rendered = renderCached(text, inline)
  const next = cut ? `${rendered}${TRUNCATION_NOTICE}` : rendered
  // html 没变就不碰 DOM：v-html 整体替换会打断用户在正文里的选中与滚动位置。
  if (next === html.value) {
    return
  }
  html.value = next
  truncated.value = cut
}

apply(props.source, props.inline, props.maxChars)

let timer: ReturnType<typeof setTimeout> | null = null

function cancelPending() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

watch(
  [() => props.source, () => props.inline, () => props.maxChars],
  ([source, inline, maxChars]) => {
    // 尾部防抖：每次变更都把上一个待办作废，只留最后一次，高频输入不会积压渲染。
    cancelPending()
    if (props.immediate) {
      apply(source, inline, maxChars)
      return
    }
    timer = setTimeout(() => {
      timer = null
      apply(source, inline, maxChars)
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
  <component
    :is="inline ? 'span' : 'div'"
    class="z-markdown"
    :class="{ 'is-truncated': truncated }"
    v-html="html"
    @click="onClick"
  ></component>
</template>

<style scoped>
.z-markdown {
  min-width: 0;
  word-break: break-word;
  line-height: 1.6;
}

.z-markdown :deep(p) {
  margin: 0 0 0.5em;
}

.z-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.z-markdown :deep(ul),
.z-markdown :deep(ol) {
  margin: 0 0 0.5em;
  padding-left: 1.4em;
}

.z-markdown :deep(a) {
  color: var(--yg-color-primary);
  text-decoration: underline;
}

/* 行内代码与代码块：等宽 + 保留换行（否则多行代码会被压成一行）。 */
.z-markdown :deep(code) {
  font-family: var(--yg-font-mono);
  font-size: 0.9em;
  white-space: pre-wrap;
}

.z-markdown :deep(pre) {
  margin: 0 0 0.5em;
  padding: var(--yg-space-2) var(--yg-space-3);
  border-radius: var(--yg-radius-sm);
  background: var(--yg-bg-surface-sunken);
  overflow-x: auto;
}

.z-markdown :deep(pre code) {
  padding: 0;
  background: none;
  font-size: 0.875em;
}

.z-markdown :deep(blockquote) {
  margin: 0 0 0.5em;
  padding-left: var(--yg-space-3);
  border-left: 3px solid var(--yg-border-default);
  color: var(--yg-text-secondary);
}

.z-markdown :deep(table) {
  width: 100%;
  margin: 0 0 0.5em;
  border-collapse: collapse;
  font-size: 0.9em;
}

.z-markdown :deep(th),
.z-markdown :deep(td) {
  padding: var(--yg-space-1) var(--yg-space-2);
  border: 1px solid var(--yg-border-default);
  text-align: left;
  vertical-align: top;
}

.z-markdown :deep(th) {
  background: var(--yg-bg-surface-sunken);
  font-weight: var(--yg-font-weight-semibold);
}

.z-markdown :deep(img) {
  max-width: 100%;
}

/* 截断提示：跟在正文末尾，弱化但看得见。 */
.z-markdown :deep(.z-markdown__truncated) {
  display: inline-block;
  margin-top: var(--yg-space-1);
  color: var(--yg-text-warning);
  font-size: var(--yg-font-size-xs);
}
</style>
