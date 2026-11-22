<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ZIcon from '@/shared/ui/ZIcon.vue'
import ZMarkdown from '@/shared/ui/ZMarkdown.vue'

withDefaults(
  defineProps<{
    placeholder?: string
    maxlength?: number
    disabled?: boolean
    rows?: number
    /** 编辑区高度，预览面板沿用同一高度以保持左右对齐 */
    minHeight?: string
  }>(),
  { maxlength: 4000, rows: 12, minHeight: '190px', disabled: false, placeholder: '' }
)

const model = defineModel<string>({ required: true })
const { t } = useI18n()

const tab = ref<'write' | 'preview'>('write')
const textarea = ref<HTMLTextAreaElement | null>(null)
const split = ref(false)

const tabs = computed(() => [
  { value: 'write', label: t('manage.notify.write') },
  { value: 'preview', label: t('manage.notify.preview') }
])

const hasContent = computed(() => model.value.trim().length > 0)

/** 便捷：在光标处包裹行内标记，选中文本时直接套用，未选中则插入占位符并选中它 */
const inlineMarks: { key: string; label: string; wrap: readonly [string, string]; hint: string }[] = [
  { key: 'bold', label: 'B', wrap: ['**', '**'], hint: '加粗' },
  { key: 'italic', label: 'I', wrap: ['*', '*'], hint: '斜体' },
  { key: 'code', label: '</>', wrap: ['`', '`'], hint: '行内代码' }
]

const blockActions = [
  { key: 'h2', label: 'H', prefix: '## ', hint: '标题' },
  { key: 'list', label: '•', prefix: '- ', hint: '列表' },
  { key: 'quote', label: '❝', prefix: '> ', hint: '引用' }
]

/** 链接是唯一带包裹的块级按钮，单独定义可保住元组类型 */
const linkWrap: readonly [string, string] = ['[', '](https://)']

function applyEdit(insert: string, wrap?: readonly [string, string]) {
  const el = textarea.value
  if (!el || el.disabled) return
  const start = el.selectionStart
  const end = el.selectionEnd
  const value = model.value

  if (wrap) {
    const selected = value.slice(start, end) || '文字'
    const next = value.slice(0, start) + wrap[0] + selected + wrap[1] + value.slice(end)
    model.value = next
    requestAnimationFrame(() => {
      el.focus()
      const offset = start + wrap[0].length
      el.setSelectionRange(offset, offset + selected.length)
    })
    return
  }

  // 行首插入：作用于光标所在整行（多行选中时逐行处理）
  const lineStart = value.lastIndexOf('\n', start - 1) + 1
  const block = value.slice(lineStart, end)
  const patched = block
    .split('\n')
    .map((line) => insert + line)
    .join('\n')
  model.value = value.slice(0, lineStart) + patched + value.slice(end)
  requestAnimationFrame(() => {
    el.focus()
    const caret = lineStart + patched.length
    el.setSelectionRange(caret, caret)
  })
}
</script>

<template>
  <div class="md-editor" :class="{ 'is-split': split, 'is-disabled': disabled }">
    <div class="md-toolbar">
      <div class="md-tabs" role="tablist">
        <button
          v-for="item in tabs"
          :key="item.value"
          type="button"
          role="tab"
          class="md-tab"
          :class="{ active: tab === item.value }"
          :aria-selected="tab === item.value"
          @click="tab = item.value as 'write' | 'preview'"
        >
          {{ item.label }}
        </button>
      </div>

      <div class="md-tools">
        <button
          v-for="mark in inlineMarks"
          :key="mark.key"
          type="button"
          class="md-tool"
          :title="mark.hint"
          :disabled="disabled"
          @click="applyEdit('', mark.wrap)"
        >
          {{ mark.label }}
        </button>
        <button
          v-for="action in blockActions"
          :key="action.key"
          type="button"
          class="md-tool"
          :title="action.hint"
          :disabled="disabled"
          @click="applyEdit(action.prefix)"
        >
          {{ action.label }}
        </button>
        <button
          type="button"
          class="md-tool"
          title="链接"
          :disabled="disabled"
          @click="applyEdit('', linkWrap)"
        >
          🔗
        </button>
        <span class="md-divider" aria-hidden="true" />
        <button
          type="button"
          class="md-tool md-tool--icon"
          :class="{ active: split }"
          :title="tab === 'preview' ? t('manage.notify.write') : t('manage.notify.preview')"
          @click="
            split = !split;
            tab = 'write'
          "
        >
          <ZIcon :name="split ? 'x' : 'eye'" :size="14" />
        </button>
      </div>
    </div>

    <div class="md-panes" :style="{ '--md-min': minHeight }">
      <textarea
        v-show="tab === 'write' || split"
        ref="textarea"
        v-model="model"
        class="md-input"
        :placeholder="placeholder"
        :disabled="disabled"
        :maxlength="maxlength"
        :rows="rows"
        spellcheck="false"
      />
      <div
        v-show="tab === 'preview' || split"
        class="md-preview"
        :class="{ 'is-empty': !hasContent }"
      >
        <ZMarkdown v-if="hasContent" :source="model" />
        <p v-else class="md-empty">{{ t('manage.notify.previewEmpty') }}</p>
      </div>
    </div>

    <div class="md-foot">
      <span class="md-hint">{{ t('manage.notify.editorHint') }}</span>
      <span class="md-count num" :class="{ 'is-near': maxlength && model.length > maxlength * 0.9 }">
        {{ t('manage.notify.charCount', { n: model.length, max: maxlength }) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.md-editor {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  box-shadow: var(--shadow-xs);
  overflow: hidden;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.md-editor:focus-within {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 14%, transparent);
}

.md-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid color-mix(in srgb, var(--line) 70%, transparent);
  background: color-mix(in srgb, var(--surface-sunken) 60%, transparent);
}

.md-tabs {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--ink) 5%, transparent);
}

.md-tab {
  padding: 0.22rem 0.7rem;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-muted);
  font-size: var(--text-xs);
  font-weight: 550;
  cursor: pointer;
  transition:
    color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.md-tab:hover {
  color: var(--ink);
}

.md-tab.active {
  color: var(--accent-ink);
  background: var(--surface-raised);
  box-shadow: var(--shadow-xs);
}

.md-tools {
  display: flex;
  align-items: center;
  gap: 2px;
  color: var(--ink-muted);
}

.md-tool {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  height: 26px;
  padding: 0 0.35rem;
  border: 0;
  border-radius: var(--radius-xs);
  background: transparent;
  color: inherit;
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
  transition:
    color var(--dur-fast) var(--ease-out),
    background var(--dur-fast) var(--ease-out);
}

.md-tool:hover:not(:disabled) {
  color: var(--ink);
  background: color-mix(in srgb, var(--ink) 7%, transparent);
}

.md-tool.active {
  color: var(--accent-ink);
  background: var(--accent-soft);
}

.md-tool:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.md-tool--icon {
  color: var(--ink-faint);
}

.md-divider {
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: var(--line);
}

.md-panes {
  display: grid;
  grid-template-columns: 1fr;
  min-height: var(--md-min);
}

.md-editor.is-split .md-panes {
  grid-template-columns: 1fr 1fr;
}

.md-input {
  width: 100%;
  min-height: var(--md-min);
  padding: 0.75rem 0.95rem;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  line-height: 1.7;
  resize: vertical;
  outline: none;
}

.md-input::placeholder {
  color: var(--ink-faint);
  font-family: var(--font-sans);
}

.md-input:disabled {
  opacity: 0.6;
}

.md-preview {
  min-height: var(--md-min);
  max-height: 420px;
  overflow: auto;
  padding: 0.75rem 0.95rem;
  border-left: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
}

.md-editor:not(.is-split) .md-preview {
  border-left: 0;
}

.md-empty {
  color: var(--ink-faint);
  font-size: var(--text-sm);
}

.md-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 0.4rem 0.75rem;
  border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
  font-size: var(--text-2xs);
  color: var(--ink-faint);
}

.md-hint {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.md-count {
  flex: none;
  font-variant-numeric: tabular-nums;
}

.md-count.is-near {
  color: var(--danger-ink, var(--c-danger));
}

@media (max-width: 640px) {
  .md-editor.is-split .md-panes {
    grid-template-columns: 1fr;
  }

  .md-editor.is-split .md-preview {
    border-left: 0;
    border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
  }

  .md-toolbar {
    flex-wrap: wrap;
    row-gap: 0.35rem;
  }

  .md-tab {
    white-space: nowrap;
  }

  .md-tools {
    margin-left: auto;
  }

  .md-hint {
    display: none;
  }

  .md-foot {
    justify-content: flex-end;
  }
}
</style>
