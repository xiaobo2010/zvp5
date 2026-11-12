<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElButtonGroup } from 'element-plus/es/components/button/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import {
  TextBold,
  TextItalic,
  Strikethrough,
  Link,
  Code,
  UnorderedList,
  OrderedList,
  Quote,
  TitleLevel,
  FileCodeOne,
  Brain
} from '@icon-park/vue-next'
import { useI18n } from 'vue-i18n'
import ZMarkdown from './ZMarkdown.vue'
import ZMarkdownAiDialog from './ZMarkdownAiDialog.vue'

const props = defineProps<{
  modelValue: string
  placeholder?: string
  maxlength?: number
  showCount?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const { t } = useI18n()

const textareaRef = ref<InstanceType<typeof ElInput>>()
const mode = ref<'edit' | 'preview'>('edit')

const showLengthCount = computed(() => props.maxlength != null && props.showCount !== false)
const isOverLimit = computed(
  () => props.maxlength != null && props.modelValue.length > props.maxlength
)
const isNearLimit = computed(
  () => props.maxlength != null && props.modelValue.length >= props.maxlength * 0.9
)

function clampToLimit(value: string): string {
  if (props.maxlength != null && value.length > props.maxlength) {
    return value.slice(0, props.maxlength)
  }
  return value
}

const currentValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', clampToLimit(val))
})

const rawTextarea = computed(() => {
  const el = textareaRef.value?.$el as HTMLElement | undefined
  return el?.querySelector('textarea') as HTMLTextAreaElement | undefined
})

function getSelection() {
  const ta = rawTextarea.value
  if (!ta) return { start: 0, end: 0, text: '' }
  return {
    start: ta.selectionStart,
    end: ta.selectionEnd,
    text: ta.value.slice(ta.selectionStart, ta.selectionEnd)
  }
}

function setEditorValue(value: string, cursor?: number) {
  const clamped = clampToLimit(value)
  const safeCursor = cursor == null ? clamped.length : Math.min(cursor, clamped.length)
  currentValue.value = clamped
  return safeCursor
}

function replaceSelection(replacement: string, cursorOffset?: number) {
  const ta = rawTextarea.value
  if (!ta) return
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const before = ta.value.slice(0, start)
  const after = ta.value.slice(end)
  const nextValue = before + replacement + after
  const nextCursor = start + (cursorOffset ?? replacement.length)
  const safeCursor = setEditorValue(nextValue, nextCursor)
  nextTick(() => {
    ta.focus()
    ta.setSelectionRange(safeCursor, safeCursor)
  })
}

function wrapSelection(before: string, after: string) {
  const { start, text } = getSelection()
  const replacement = text.length ? before + text + after : before + after
  const cursor = text.length ? start + replacement.length : start + before.length
  replaceSelection(replacement, cursor)
}

function prefixLines(prefix: string) {
  const ta = rawTextarea.value
  if (!ta) return
  const value = ta.value
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const lineStart = value.lastIndexOf('\n', start - 1) + 1
  const lineEndIndex = value.indexOf('\n', end)
  const blockEnd = lineEndIndex === -1 ? value.length : lineEndIndex
  const block = value.slice(lineStart, blockEnd)
  const lines = block.split('\n')
  const already = lines.filter((l) => l.trim().length > 0).every((l) => l.startsWith(prefix))
  const newBlock = already
    ? lines.map((l) => (l.startsWith(prefix) ? l.slice(prefix.length) : l)).join('\n')
    : lines.map((l) => (l.trim().length > 0 ? prefix + l : l)).join('\n')
  const nextValue = value.slice(0, lineStart) + newBlock + value.slice(blockEnd)
  const nextCursor = lineStart + newBlock.length
  const safeCursor = setEditorValue(nextValue, nextCursor)
  nextTick(() => {
    ta.focus()
    ta.setSelectionRange(safeCursor, safeCursor)
  })
}

function orderedList() {
  const ta = rawTextarea.value
  if (!ta) return
  const value = ta.value
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const lineStart = value.lastIndexOf('\n', start - 1) + 1
  const lineEndIndex = value.indexOf('\n', end)
  const blockEnd = lineEndIndex === -1 ? value.length : lineEndIndex
  const block = value.slice(lineStart, blockEnd)
  const lines = block.split('\n')
  const olRegex = /^\d+\.\s+/
  const already = lines.filter((l) => l.trim().length > 0).every((l) => olRegex.test(l))
  let number = 1
  const newBlock = lines
    .map((l) => {
      if (l.trim().length === 0) return l
      if (already) return l.replace(olRegex, '')
      const result = `${number}. ${l}`
      number++
      return result
    })
    .join('\n')
  const nextValue = value.slice(0, lineStart) + newBlock + value.slice(blockEnd)
  const nextCursor = lineStart + newBlock.length
  const safeCursor = setEditorValue(nextValue, nextCursor)
  nextTick(() => {
    ta.focus()
    ta.setSelectionRange(safeCursor, safeCursor)
  })
}

function heading() {
  const ta = rawTextarea.value
  if (!ta) return
  const value = ta.value
  const start = ta.selectionStart
  const lineStart = value.lastIndexOf('\n', start - 1) + 1
  const lineEndIndex = value.indexOf('\n', start)
  const blockEnd = lineEndIndex === -1 ? value.length : lineEndIndex
  const line = value.slice(lineStart, blockEnd)
  const match = /^(#{1,6})\s+(.*)$/.exec(line)
  const newLine = match ? match[2] : `### ${line}`
  const nextValue = value.slice(0, lineStart) + newLine + value.slice(blockEnd)
  const nextCursor = lineStart + newLine.length
  const safeCursor = setEditorValue(nextValue, nextCursor)
  nextTick(() => {
    ta.focus()
    ta.setSelectionRange(safeCursor, safeCursor)
  })
}

function link() {
  const { start, text } = getSelection()
  const label = text.length ? text : t('markdown.editor.linkLabel')
  const replacement = `[${label}]()`
  const cursor = start + (text.length ? label.length + 3 : 1)
  replaceSelection(replacement, cursor)
}

function codeBlock() {
  const { start, text } = getSelection()
  const before = '```\n'
  const after = '\n```'
  const replacement = text.length ? before + text + after : before + after
  const cursor = start + (text.length ? replacement.length : before.length)
  replaceSelection(replacement, cursor)
}

// AI 写作助手：打开弹窗时把「选中的范围」快照下来。弹窗拿走了焦点，等到用户点
// 「应用」时 textarea 的 selectionStart 已经不可信，只能按快照回写。
const aiVisible = ref(false)
const aiSource = ref('')
const aiHasSelection = ref(false)
const aiRange = ref({ start: 0, end: 0 })

function openAi() {
  const { start, end, text } = getSelection()
  aiRange.value = { start, end }
  aiHasSelection.value = text.length > 0
  aiSource.value = text.length > 0 ? text : currentValue.value
  aiVisible.value = true
}

function applyAi(text: string) {
  const value = currentValue.value
  const start = Math.min(aiRange.value.start, value.length)
  const end = Math.min(Math.max(aiRange.value.end, start), value.length)
  const nextValue = value.slice(0, start) + text + value.slice(end)
  const cursor = start + text.length
  const safeCursor = setEditorValue(nextValue, cursor)
  nextTick(() => {
    const ta = rawTextarea.value
    if (!ta) return
    ta.focus()
    ta.setSelectionRange(safeCursor, safeCursor)
  })
}

const actions = [
  {
    id: 'bold',
    icon: TextBold,
    titleKey: 'markdown.editor.bold',
    handler: () => wrapSelection('**', '**')
  },
  {
    id: 'italic',
    icon: TextItalic,
    titleKey: 'markdown.editor.italic',
    handler: () => wrapSelection('*', '*')
  },
  {
    id: 'strikethrough',
    icon: Strikethrough,
    titleKey: 'markdown.editor.strikethrough',
    handler: () => wrapSelection('~~', '~~')
  },
  {
    id: 'inlineCode',
    icon: Code,
    titleKey: 'markdown.editor.inlineCode',
    handler: () => wrapSelection('`', '`')
  },
  { id: 'link', icon: Link, titleKey: 'markdown.editor.link', handler: link },
  {
    id: 'bulletList',
    icon: UnorderedList,
    titleKey: 'markdown.editor.bulletList',
    handler: () => prefixLines('- ')
  },
  {
    id: 'orderedList',
    icon: OrderedList,
    titleKey: 'markdown.editor.orderedList',
    handler: orderedList
  },
  { id: 'quote', icon: Quote, titleKey: 'markdown.editor.quote', handler: () => prefixLines('> ') },
  { id: 'heading', icon: TitleLevel, titleKey: 'markdown.editor.heading', handler: heading },
  { id: 'codeBlock', icon: FileCodeOne, titleKey: 'markdown.editor.codeBlock', handler: codeBlock }
]
</script>

<template>
  <div class="markdown-editor">
    <div class="markdown-editor__toolbar" @pointerdown.prevent @mousedown.prevent>
      <ElButton
        v-for="action in actions"
        :key="action.id"
        size="small"
        text
        :icon="action.icon"
        :title="t(action.titleKey)"
        @click="action.handler"
      />
      <ElButton
        size="small"
        text
        :icon="Brain"
        :title="t('markdown.editor.ai.open')"
        @click="openAi"
      />
      <div
        v-if="showLengthCount"
        class="markdown-editor__count"
        :class="{ 'is-over-limit': isOverLimit, 'is-near-limit': isNearLimit && !isOverLimit }"
        :title="isOverLimit ? t('markdown.editor.overLimit') : ''"
      >
        {{ t('markdown.editor.count', { current: modelValue.length, max: maxlength }) }}
      </div>
    </div>

    <div class="markdown-editor__body">
      <div class="markdown-editor__toggle">
        <ElButtonGroup>
          <ElButton
            :type="mode === 'edit' ? 'primary' : 'default'"
            size="small"
            @click="mode = 'edit'"
          >
            {{ t('markdown.editor.edit') }}
          </ElButton>
          <ElButton
            :type="mode === 'preview' ? 'primary' : 'default'"
            size="small"
            @click="mode = 'preview'"
          >
            {{ t('markdown.editor.preview') }}
          </ElButton>
        </ElButtonGroup>
      </div>

      <div class="markdown-editor__panes">
        <div
          class="markdown-editor__pane markdown-editor__edit"
          :class="{ 'is-active': mode === 'edit' }"
        >
          <ElInput
            ref="textareaRef"
            v-model="currentValue"
            type="textarea"
            resize="none"
            :placeholder="placeholder ?? t('markdown.editor.placeholder')"
            :maxlength="maxlength"
            class="markdown-editor__textarea"
          />
        </div>

        <div
          class="markdown-editor__pane markdown-editor__preview"
          :class="{ 'is-active': mode === 'preview' }"
        >
          <ZMarkdown :source="currentValue" class="h-full w-full" />
        </div>
      </div>
    </div>

    <ZMarkdownAiDialog
      v-model:visible="aiVisible"
      :source="aiSource"
      :has-selection="aiHasSelection"
      @apply="applyAi"
    />
  </div>
</template>

<style scoped>
.markdown-editor {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.markdown-editor__toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  align-items: center;
}

.markdown-editor__count {
  margin-left: auto;
  font-size: 0.75rem;
  line-height: 1;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.markdown-editor__count.is-near-limit {
  color: var(--el-color-warning);
}

.markdown-editor__count.is-over-limit {
  color: var(--el-color-danger);
}

.markdown-editor__body {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.markdown-editor__toggle {
  display: flex;
  gap: 0.5rem;
}

.markdown-editor__panes {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.markdown-editor__pane {
  display: none;
  flex: 1 1 0;
  min-height: 220px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  overflow: auto;
}

.markdown-editor__pane.is-active {
  display: flex;
}

.markdown-editor__edit {
  flex-direction: column;
}

.markdown-editor__preview {
  padding: 0.75rem;
}

.markdown-editor__textarea {
  flex: 1;
}

.markdown-editor__textarea :deep(.el-textarea__inner) {
  height: 100%;
  min-height: 220px;
  border: none;
  box-shadow: none;
}

@media (min-width: 640px) {
  .markdown-editor__toggle {
    display: none;
  }

  .markdown-editor__panes {
    flex-direction: row;
  }

  .markdown-editor__pane {
    display: flex;
    width: 50%;
  }
}
</style>
