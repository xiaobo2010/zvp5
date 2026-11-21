<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ source: string }>()

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true
})

const html = computed(() => DOMPurify.sanitize(md.render(props.source || '')))
</script>

<template>
  <div class="markdown" v-html="html" />
</template>

<style scoped>
.markdown {
  font-size: var(--text-sm);
  line-height: 1.72;
  color: var(--ink-soft);
  word-break: break-word;
}

.markdown :deep(p) {
  margin: 0 0 0.7em;
}

.markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown :deep(a) {
  color: var(--accent-ink);
}

.markdown :deep(ul),
.markdown :deep(ol) {
  margin: 0 0 0.7em;
  padding-left: 1.3em;
}

.markdown :deep(code) {
  padding: 0.1em 0.35em;
  border-radius: 5px;
  background: color-mix(in srgb, var(--ink) 7%, transparent);
  font-family: var(--font-mono);
  font-size: 0.92em;
}

.markdown :deep(pre) {
  overflow: auto;
  padding: 0.8em 1em;
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
}

.markdown :deep(blockquote) {
  margin: 0 0 0.7em;
  padding-left: 1em;
  border-left: 2px solid var(--line-strong);
  color: var(--ink-muted);
}

.markdown :deep(h1),
.markdown :deep(h2),
.markdown :deep(h3) {
  margin: 1em 0 0.5em;
  font-family: var(--font-sans);
  font-size: 1em;
  font-weight: 650;
}

.markdown :deep(img) {
  border-radius: var(--radius-sm);
}
</style>
