<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listSettings,
  updateSettings,
  settingErrorMessage,
  type SettingValue,
  type SystemSettingItem
} from '@/api/next/system/settings'

/**
 * 系统设置：把后端下发的可改项按 group（ai / ops）分组渲染。
 * 只提交发生变化的键；值类型决定控件（int / bool / str / list / dict）。
 */
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const items = ref<SystemSettingItem[]>([])
const defaults = ref<Record<string, SettingValue>>({})
/** key → 当前编辑值（字符串形态，保存时按 type 还原） */
const draft = ref<Record<string, string>>({})
const dirty = ref<Set<string>>(new Set())

const groups = computed(() => {
  const map = new Map<string, SystemSettingItem[]>()
  for (const item of items.value) {
    const list = map.get(item.group) ?? []
    list.push(item)
    map.set(item.group, list)
  }
  return [...map.entries()].map(([group, list]) => ({ group, list }))
})

function toText(item: SystemSettingItem, value: SettingValue): string {
  if (item.type === 'bool') {
    return value ? 'true' : 'false'
  }
  if (item.type === 'dict' || item.type === 'list') {
    return typeof value === 'string' ? value : JSON.stringify(value ?? null)
  }
  return value === null || value === undefined ? '' : String(value)
}

function parseValue(item: SystemSettingItem, text: string): SettingValue {
  if (item.type === 'bool') {
    return text === 'true'
  }
  if (item.type === 'int') {
    const n = Number(text)
    return Number.isFinite(n) ? n : 0
  }
  if (item.type === 'dict') {
    try {
      return JSON.parse(text) as SettingValue
    } catch {
      return text
    }
  }
  if (item.type === 'list') {
    // list 既可能是 JSON 数组，也可能是单选枚举
    if (item.options?.length) {
      return text
    }
    try {
      return JSON.parse(text) as SettingValue
    } catch {
      return text.split(',').map((s) => s.trim()).filter(Boolean)
    }
  }
  return text
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listSettings()
    items.value = Array.isArray(data.items) ? data.items : []
    defaults.value = data.defaults ?? {}
    draft.value = Object.fromEntries(items.value.map((i) => [i.key, toText(i, i.value)]))
    dirty.value = new Set()
  } catch (e) {
    error.value = settingErrorMessage(e, '读取设置失败')
  } finally {
    loading.value = false
  }
}

function touch(key: string) {
  dirty.value = new Set(dirty.value).add(key)
}

async function save() {
  if (!dirty.value.size) {
    ElMessage.info('没有需要保存的改动')
    return
  }
  saving.value = true
  try {
    const payload = items.value
      .filter((item) => dirty.value.has(item.key))
      .map((item) => ({ key: item.key, value: parseValue(item, draft.value[item.key] ?? '') }))
    await updateSettings(payload)
    ElMessage.success(`已保存 ${payload.length} 项设置`)
    await load()
  } catch (e) {
    ElMessage.error(settingErrorMessage(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

function reset(key: string) {
  const item = items.value.find((i) => i.key === key)
  if (!item) {
    return
  }
  const fallback = defaults.value[key] ?? item.value
  draft.value[key] = toText(item, fallback)
  touch(key)
}

onMounted(load)
</script>

<template>
  <section class="sys">
    <header class="sys__head">
      <div>
        <h2 class="sys__title">系统设置</h2>
        <p class="sys__hint">只提交改动过的项；数值项有上下界，越界会被后端拒绝并给出原因。</p>
      </div>
      <div class="sys__actions">
        <button class="yg2-btn yg2-btn--quiet" type="button" :disabled="loading" @click="load">
          刷新
        </button>
        <button
          class="yg2-btn yg2-btn--primary"
          type="button"
          :disabled="saving || !dirty.size"
          @click="save"
        >
          {{ saving ? '保存中…' : `保存改动（${dirty.size}）` }}
        </button>
      </div>
    </header>

    <p v-if="error" class="sys__error">{{ error }}</p>

    <div v-for="g in groups" :key="g.group" class="sys__group">
      <h3 class="sys__group-title">{{ g.group === 'ai' ? 'AI 相关' : '运维相关' }}</h3>
      <div v-for="item in g.list" :key="item.key" class="sys__row" :class="{ 'is-dirty': dirty.has(item.key) }">
        <div class="sys__label">
          <span class="sys__name">{{ item.label || item.key }}</span>
          <code class="sys__key">{{ item.key }}</code>
          <span v-if="item.description" class="sys__desc">{{ item.description }}</span>
        </div>
        <div class="sys__control">
          <label v-if="item.type === 'bool'" class="sys__switch">
            <input
              type="checkbox"
              :checked="draft[item.key] === 'true'"
              @change="((draft[item.key] = ($event.target as HTMLInputElement).checked ? 'true' : 'false'), touch(item.key))"
            />
            <span>{{ draft[item.key] === 'true' ? '开启' : '关闭' }}</span>
          </label>
          <select
            v-else-if="item.type === 'list' && item.options?.length"
            class="yg2-input sys__input"
            :value="draft[item.key]"
            @change="((draft[item.key] = ($event.target as HTMLSelectElement).value), touch(item.key))"
          >
            <option v-for="opt in item.options" :key="opt" :value="opt">{{ opt }}</option>
          </select>
          <input
            v-else-if="item.type === 'int'"
            class="yg2-input sys__input"
            type="number"
            :min="item.min ?? undefined"
            :max="item.max ?? undefined"
            :value="draft[item.key]"
            @input="((draft[item.key] = ($event.target as HTMLInputElement).value), touch(item.key))"
          />
          <textarea
            v-else-if="item.type === 'dict' || item.type === 'list'"
            class="yg2-input sys__input sys__textarea"
            rows="2"
            :value="draft[item.key]"
            @input="((draft[item.key] = ($event.target as HTMLTextAreaElement).value), touch(item.key))"
          />
          <input
            v-else
            class="yg2-input sys__input"
            :value="draft[item.key]"
            @input="((draft[item.key] = ($event.target as HTMLInputElement).value), touch(item.key))"
          />
          <button class="sys__reset" type="button" :disabled="!dirty.has(item.key)" @click="reset(item.key)">
            恢复默认
          </button>
        </div>
      </div>
    </div>

    <p v-if="!loading && !items.length && !error" class="sys__empty">后端没有返回可改的设置项。</p>
  </section>
</template>

<style scoped>
.sys {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sys__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.sys__title {
  margin: 0 0 4px;
  font-size: 17px;
  font-weight: 600;
}

.sys__hint {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.sys__actions {
  display: flex;
  gap: 8px;
  flex: none;
}

.sys__error {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-danger);
}

.sys__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-top: 1px solid var(--yg2-sep);
  padding-top: 10px;
}

.sys__group-title {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--yg2-text-secondary);
}

.sys__row {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(260px, 420px);
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--yg2-sep);
}

.sys__row.is-dirty {
  background: color-mix(in srgb, var(--yg2-accent) 4%, transparent);
}

.sys__label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.sys__name {
  font-size: 14px;
  font-weight: 500;
}

.sys__key {
  font-size: 11.5px;
  color: var(--yg2-text-tertiary);
  font-family: var(--yg2-font-mono, ui-monospace, SFMono-Regular, monospace);
}

.sys__desc {
  font-size: 12px;
  color: var(--yg2-text-secondary);
}

.sys__control {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sys__input {
  flex: 1 1 auto;
  min-width: 0;
  height: 34px;
}

.sys__textarea {
  height: auto;
  padding: 8px 10px;
  resize: vertical;
}

.sys__switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.sys__reset {
  flex: none;
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: 999px;
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.sys__reset:disabled {
  opacity: 0.45;
  cursor: default;
}

.sys__empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-text-tertiary);
}

@media (max-width: 900px) {
  .sys__row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
