<script setup lang="ts">
import { useUiStore } from '@/shared/stores/ui'
import ZIcon from './ZIcon.vue'

const ui = useUiStore()
</script>

<template>
  <div class="viewport" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="toast in ui.toasts" :key="toast.id" class="toast glass" :class="`tone-${toast.tone}`">
        <ZIcon :name="toast.tone === 'danger' ? 'alert' : toast.tone === 'success' ? 'check' : 'sparkles'" :size="16" />
        <span>{{ toast.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.viewport {
  position: fixed;
  left: 50%;
  bottom: calc(var(--space-6) + env(safe-area-inset-bottom, 0px));
  z-index: 90;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: center;
  transform: translateX(-50%);
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.65rem 1rem;
  border-radius: var(--radius-pill);
  font-size: var(--text-sm);
  color: var(--ink);
  max-width: min(92vw, 32rem);
}

.tone-success {
  color: var(--c-success);
}

.tone-danger {
  color: var(--c-danger);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(10px) scale(0.98);
}
</style>
