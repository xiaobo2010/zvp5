<script lang="ts" setup>
import { toRefs, ref, watch } from 'vue'
import type { Component as VueComponent } from 'vue'
import { ElButton, ElDialog, ElCard } from 'element-plus'

const props = withDefaults(
  defineProps<{
    size?: 'large' | 'default' | 'small'
    popType?: 'dialog'
    mode: 'card' | 'button'
    width?: string
    direction?: 'rtl' | 'ltr' | 'ttb' | 'btt'
    loading?: boolean
    type?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'text' | ''
    icon?: VueComponent
    fullscreen?: boolean
    modal?: boolean
    title?: string
    center?: boolean
    round?: boolean
    open?: boolean
    disabled?: boolean
    buttonLoading?: boolean
    wFull?: boolean
  }>(),
  {
    size: 'small',
    popType: 'dialog',
    width: '60%',
    direction: 'rtl',
    type: '',
    fullscreen: false,
    modal: true,
    title: undefined,
    center: true,
    round: true,
    open: false,
    disabled: false,
    buttonLoading: false,
    wFull: false
  }
)
const emits = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'click', value: boolean): void
}>()

const {
  size,
  popType,
  mode,
  width,
  direction,
  loading,
  type,
  icon,
  fullscreen,
  modal,
  title,
  center,
  round,
  open,
  buttonLoading
} = toRefs(props)

const show = ref(false)

watch(
  open,
  () => {
    show.value = open.value
  },
  { immediate: true }
)

watch(
  show,
  () => {
    emits('update:open', show.value)
  },
  { immediate: true }
)

function startOpen() {
  emits('click', true)
  show.value = true
}
</script>

<template>
  <ElButton
    v-if="mode === 'button'"
    :round="round"
    text
    bg
    :size="size"
    :type="type ?? 'primary'"
    :icon="icon"
    @click="startOpen"
    :disabled="disabled"
    v-bind="$attrs"
    v-on="$attrs"
    :loading="buttonLoading"
  >
    <slot name="text" />
  </ElButton>
  <Teleport to="body" v-if="show">
    <ElDialog
      v-if="popType === 'dialog'"
      v-model="show"
      :fullscreen="fullscreen"
      :modal="modal ? modal : true"
      :title="title"
      :width="width"
      :center="center ? center : true"
    >
      <slot name="default" />

      <template #header>
        <slot name="header" />
      </template>
    </ElDialog>
  </Teleport>
  <div v-if="mode === 'card'">
    <ElCard
      v-if="mode === 'card'"
      shadow="never"
      v-loading="loading"
      :class="[wFull ? 'w-full' : '']"
    >
      <slot name="default" />
    </ElCard>
  </div>
</template>
