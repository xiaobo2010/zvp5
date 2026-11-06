<script setup lang="ts">
import { ElSelect, ElOption } from 'element-plus'
import { computed, onMounted, ref, toRefs, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '@/api'
import { useGroupsStore } from '@/stores/groups'
import { extractHanCharacters, extractNonHanCharacters } from '@/utils/extraChars'
import type { User } from '@/../types'

interface PersonOption {
  label: string
  value: string
  number: number
  class: string
}

const props = defineProps<{
  modelValue: string | string[]
  filterStart: number // Digits, 6-8
  fullWidth?: boolean
  disabled?: boolean
  multiple?: boolean
  departmentOnly?: boolean
}>()
const emits = defineEmits<{
  (e: 'update:modelValue', value: string | string[]): void
  (e: 'select', person: PersonOption | null): void
}>()

const { t } = useI18n()
const { modelValue, filterStart, fullWidth, disabled, multiple, departmentOnly } = toRefs(props)

const id = ref<string | string[]>(modelValue.value)
const options = ref<PersonOption[]>([])
const selectedOptions = ref<PersonOption[]>([])
const activeQuery = ref(false)
const hint = ref('')
const load = ref(false)

const cache = new Map<string, PersonOption>()

const emptyText = computed(() => {
  if (load.value) {
    return t('ai.select.searching')
  }
  return t(hint.value || 'ai.select.noMatch', { n: filterStart.value })
})

const displayOptions = computed<PersonOption[]>(() => {
  if (activeQuery.value) {
    return options.value
  }
  const kept = new Set(selectedOptions.value.map((item) => item.value))
  return [...selectedOptions.value, ...options.value.filter((item) => !kept.has(item.value))]
})

function sameValue(a: string | string[], b: string | string[]) {
  return Array.isArray(a) || Array.isArray(b)
    ? JSON.stringify(a) === JSON.stringify(b)
    : a === b
}

async function getUserGroups(user: User) {
  const groupsStore = useGroupsStore()
  return await Promise.all(user.group.map((group) => groupsStore.fetchGroup(group)))
}

async function toOption(user: User): Promise<PersonOption> {
  let className = ''
  try {
    const groups = await getUserGroups(user)
    className =
      groups.filter((group) => group?._id).find((group) => group?.type === 'class')?.name || ''
  } catch {
    className = ''
  }
  return {
    label: user.name,
    value: user._id,
    number: user.id,
    class: className
  }
}

function syncSelectedOptions() {
  const values = Array.isArray(id.value) ? id.value : id.value ? [id.value] : []
  selectedOptions.value = values
    .map((value) => cache.get(value))
    .filter((item): item is PersonOption => !!item)
}

async function readPerson(value: string): Promise<PersonOption | null> {
  try {
    const user = await api.user.readOne(value)
    if (!user) {
      return null
    }
    const option = await toOption(user)
    cache.set(option.value, option)
    return option
  } catch {
    return null
  }
}

async function ensureSelected(value: string | string[]) {
  const values = Array.isArray(value) ? value : value ? [value] : []
  const missing = values.filter((item) => !cache.has(item))
  if (missing.length) {
    await Promise.all(missing.map(readPerson))
  }
  syncSelectedOptions()
}

let querySeq = 0

async function filter(query: string) {
  const keyword = query.trim()
  if (!keyword) {
    activeQuery.value = false
    hint.value = ''
    options.value = []
    return
  }
  activeQuery.value = true
  const extra = extractNonHanCharacters(keyword).replace(/[0-9]/g, '').length
  const digits = keyword.replace(/[^0-9]/g, '').length
  const han = extractHanCharacters(keyword).length
  if (extra > 0 || (digits > 0 && han > 0)) {
    options.value = []
    hint.value = 'ai.select.invalidChars'
    return
  }
  if (!departmentOnly.value && filterStart.value) {
    if (digits > 0) {
      if (digits < filterStart.value) {
        options.value = []
        hint.value = 'ai.select.needMoreDigits'
        return
      }
    } else if (han < 2) {
      options.value = []
      hint.value = 'ai.select.needMoreName'
      return
    }
  }
  hint.value = ''
  const seq = ++querySeq
  load.value = true
  try {
    const result = await api.user.read(keyword, 1, departmentOnly.value ? 20 : 5)
    if (seq !== querySeq) {
      return
    }
    const users = result?.users ?? []
    const filtered = departmentOnly.value
      ? users.filter((user) => !!user.group.find((group) => group === '65e6fa210edc81d012ec41b6'))
      : users
    const mapped = await Promise.all(filtered.map(toOption))
    if (seq !== querySeq) {
      return
    }
    for (const option of mapped) {
      cache.set(option.value, option)
    }
    options.value = mapped
  } catch {
    if (seq !== querySeq) {
      return
    }
    hint.value = 'ai.select.searchFailed'
  } finally {
    if (seq === querySeq) {
      load.value = false
    }
  }
}

function onChange(value: string | string[]) {
  activeQuery.value = false
  hint.value = ''
  options.value = []
  const values = Array.isArray(value) ? value : value ? [value] : []
  const persons = values
    .map((item) => cache.get(item))
    .filter((item): item is PersonOption => !!item)
  emits('select', Array.isArray(value) ? null : (persons[0] ?? null))
}

watch(id, (value) => {
  const normalized: string | string[] = value ?? ''
  if (!sameValue(normalized, modelValue.value)) {
    emits('update:modelValue', normalized)
  }
  syncSelectedOptions()
})

watch(modelValue, (value) => {
  if (!sameValue(value, id.value)) {
    id.value = value
  }
  void ensureSelected(value)
})

onMounted(() => {
  void ensureSelected(modelValue.value)
})
</script>

<template>
  <ElSelect
    v-model="id"
    :disabled="disabled"
    :style="{ width: fullWidth ? '100%' : 'auto' }"
    :filter-method="filter"
    :loading="load"
    filterable
    :multiple="multiple"
    remote
    class="w-full"
    size="default"
    remote-show-suffix
    popper-class="yg-select-person__selector"
    :popper-append-to-body="true"
    @change="onChange"
    v-bind="$attrs"
  >
    <ElOption
      v-for="item in displayOptions"
      :key="item.value"
      :label="item.label"
      :value="item.value"
      filterable
      remote
    >
      <span style="float: left">{{ item.label }}</span>
      <span style="float: right; color: var(--yg-text-tertiary); font-size: var(--yg-font-size-sm)">
        {{ item.number }}, {{ item.class }}
      </span>
    </ElOption>
    <template #empty>
      <p class="yg-select-person__hint">{{ emptyText }}</p>
    </template>
    <template #prefix>
      <slot name="prepend"></slot>
    </template>
  </ElSelect>
</template>

<style>
.yg-select-person__selector .el-select-dropdown__wrap {
  max-height: 182px;
}

.yg-select-person__hint {
  padding: 4px 12px;
  font-size: var(--yg-font-size-sm);
  color: var(--yg-text-tertiary);
}
</style>
