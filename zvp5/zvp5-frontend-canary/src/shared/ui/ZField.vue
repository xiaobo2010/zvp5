<script setup lang="ts">
import { computed, ref } from 'vue'
import ZIcon from './ZIcon.vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    id?: string
    label?: string
    placeholder?: string
    type?: 'text' | 'password' | 'search' | 'tel'
    hint?: string
    error?: string
    autocomplete?: string
    inputmode?: 'text' | 'numeric' | 'decimal'
    disabled?: boolean
    allowReveal?: boolean
    icon?: string
    size?: 'sm' | 'md' | 'lg'
  }>(),
  {
    type: 'text',
    size: 'md',
    disabled: false,
    allowReveal: false
  }
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  blur: []
  enter: []
}>()

const revealed = ref(false)
const inputType = computed(() => {
  if (props.type !== 'password') return props.type
  return revealed.value ? 'text' : 'password'
})

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <label class="z-field" :class="{ 'has-error': Boolean(error) }">
    <span v-if="label" class="label">{{ label }}</span>
    <span class="control" :class="`size-${size}`">
      <ZIcon v-if="icon" :name="icon" :size="18" class="leading" />
      <input
        :id="id"
        :value="modelValue"
        :type="inputType"
        :placeholder="placeholder"
        :autocomplete="autocomplete"
        :inputmode="inputmode"
        :disabled="disabled"
        @input="onInput"
        @blur="emit('blur')"
        @keydown.enter="emit('enter')"
      />
      <button
        v-if="type === 'password' && allowReveal"
        type="button"
        class="reveal"
        :aria-label="revealed ? '隐藏密码' : '显示密码'"
        @click="revealed = !revealed"
      >
        <ZIcon :name="revealed ? 'eye-off' : 'eye'" :size="18" />
      </button>
    </span>
    <span v-if="error" class="message error">{{ error }}</span>
    <span v-else-if="hint" class="message hint">{{ hint }}</span>
  </label>
</template>

<style scoped>
.z-field {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 100%;
}

.label {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--ink-soft);
}

.control {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-height: 48px;
  padding: 0 0.95rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
  background: var(--surface-raised);
  box-shadow: var(--shadow-xs);
  transition:
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    background-color var(--dur-fast) var(--ease-out);
}

.size-sm {
  min-height: 40px;
  border-radius: var(--radius-sm);
}

.size-lg {
  min-height: 54px;
}

.control:focus-within {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line));
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 14%, transparent);
}

.has-error .control {
  border-color: color-mix(in srgb, var(--c-danger) 60%, var(--line));
}

.has-error .control:focus-within {
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--c-danger) 14%, transparent);
}

.leading {
  color: var(--ink-faint);
  flex: none;
}

input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  font-size: var(--text-md);
  color: var(--ink);
  padding: 0.65rem 0;
}

input::placeholder {
  color: var(--ink-faint);
}

input:disabled {
  cursor: not-allowed;
  color: var(--ink-muted);
}

.reveal {
  flex: none;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-xs);
  color: var(--ink-faint);
  transition: color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}

.reveal:hover {
  color: var(--ink-soft);
  background: color-mix(in srgb, var(--ink) 6%, transparent);
}

.message {
  font-size: var(--text-xs);
  line-height: 1.5;
}

.hint {
  color: var(--ink-faint);
}

.error {
  color: var(--c-danger);
}
</style>
