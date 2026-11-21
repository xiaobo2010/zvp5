<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import ZGlassSurface from './ZGlassSurface.vue'
import ZIcon from './ZIcon.vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    description?: string
    size?: 'sm' | 'md' | 'lg'
    hideClose?: boolean
  }>(),
  { size: 'md', hideClose: false }
)

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

function close() {
  emit('update:modelValue', false)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

watch(
  () => props.modelValue,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
    if (open) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  }
)

onBeforeUnmount(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="modelValue" class="overlay" @click.self="close">
        <div
          class="panel glass-host"
          :class="`size-${size}`"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <ZGlassSurface material="frosted" depth="lens" :track="true" />

          <div v-if="title || !hideClose" class="head">
            <div class="titles">
              <h2 v-if="title" class="title">{{ title }}</h2>
              <p v-if="description" class="desc">{{ description }}</p>
            </div>
            <button v-if="!hideClose" type="button" class="close" aria-label="关闭" @click="close">
              <ZIcon name="x" :size="18" />
            </button>
          </div>
          <div class="body"><slot /></div>
          <div v-if="$slots.footer" class="footer"><slot name="footer" /></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: grid;
  place-items: center;
  padding: var(--space-4);
  background: var(--mask);
  -webkit-backdrop-filter: blur(8px) saturate(120%);
  backdrop-filter: blur(8px) saturate(120%);
}

.panel {
  position: relative;
  width: 100%;
  max-height: 86dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: var(--radius-xl);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
}

.size-sm {
  max-width: 420px;
}

.size-md {
  max-width: 560px;
}

.size-lg {
  max-width: 760px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-6) var(--space-6) var(--space-3);
}

.titles {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 0;
}

.title {
  font-size: var(--text-xl);
}

.desc {
  font-size: var(--text-sm);
  color: var(--ink-muted);
  line-height: 1.55;
}

.close {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 50%;
  color: var(--ink-muted);
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-micro) var(--spring-snappy);
}

.close:hover {
  background: color-mix(in srgb, var(--glass-hi) 40%, transparent);
  color: var(--ink);
  transform: scale(1.05);
}

.close:active {
  transform: scale(0.9);
}

.body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  padding: var(--space-2) var(--space-6) var(--space-6);
  scrollbar-gutter: stable;
}

.footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-6) var(--space-6);
  border-top: 1px solid color-mix(in srgb, var(--glass-border) 45%, transparent);
}

/* --- 进入：弹簧上浮 + 轻微放大；退出：快速收敛 ------------------------- */
.modal-enter-active {
  transition: opacity 260ms var(--ease-out);
}

.modal-enter-active .panel {
  transition:
    transform 500ms var(--spring-snappy),
    opacity 260ms var(--ease-out);
}

.modal-leave-active {
  transition: opacity 180ms var(--ease-out);
}

.modal-leave-active .panel {
  transition:
    transform 180ms var(--ease-out),
    opacity 150ms var(--ease-out);
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .panel {
  opacity: 0;
  transform: translateY(18px) scale(0.96);
}

.modal-leave-to .panel {
  opacity: 0;
  transform: translateY(8px) scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .modal-enter-from .panel,
  .modal-leave-to .panel {
    transform: none;
  }
}
</style>
