<script setup lang="ts">
import { ElSelect, ElOption } from 'element-plus'
import { ref, toRefs, watch } from 'vue'
import api from '@/api'
import type { Group } from '@/../types'
import { useUserStore } from '@/stores/user'

const props = withDefaults(
  defineProps<{
    modelValue: string
    fullWidth?: boolean
    clearable?: boolean
  }>(),
  {
    fullWidth: true,
    clearable: false
  }
)
const emits = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const { modelValue, fullWidth, clearable } = toRefs(props)

const classGroupID = ref<string>(modelValue.value)
watch(classGroupID, () => emits('update:modelValue', classGroupID.value), { immediate: true })
watch(
  () => classGroupID,
  () => emits('update:modelValue', classGroupID.value),
  { immediate: true }
)
const classes = ref<Group[]>([])

const load = ref(false)

const userStore = useUserStore()

async function remoteFetchClass(search: string) {
  const position = userStore.position
  if (position.includes('admin') || position.includes('department')) {
    if (search) {
      const classesSet = await api.group.read_v2(1, 50, search)
      classes.value = classesSet ? classesSet.groups.filter((g) => g.type !== 'permission') : []
    } else if (classGroupID.value) {
      const group = await api.group.readOne(classGroupID.value)
      classes.value = group ? [group] : []
    } else {
      classes.value = []
    }
  } else if (position.includes('secretary') && userStore.class_id) {
    const group = await api.group.readOne(userStore.class_id)
    classes.value = group ? [group] : []
  } else {
    const classesSet = await api.group.read('class', 1, 50, search)
    classes.value = classesSet
      ? classesSet.groups.filter((group) => group.type === 'class')
      : []
  }
}
</script>

<template>
  <ElSelect
    v-model="classGroupID"
    :style="{ width: fullWidth ? '100%' : 'auto' }"
    :filter-method="remoteFetchClass"
    :loading="load"
    filterable
    remote
    remote-show-suffix
    class="w-full"
    size="default"
    v-bind="$attrs"
    :clearable="clearable"
  >
    <ElOption
      v-for="item in classes"
      :key="item._id"
      :label="item.name"
      :value="item._id"
      filterable
      remote
    >
      <span style="float: left">{{ item.name }}</span>
    </ElOption>
    <template #prefix>
      <slot name="prepend"></slot>
    </template>
  </ElSelect>
</template>
