<script setup lang="ts">
import { useUiStore } from '@/shared/stores/ui'
import ZGlassSurface from './ZGlassSurface.vue'
import ZIcon from './ZIcon.vue'

const ui = useUiStore()
</script>

<template>
  <div class="viewport" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="toast in ui.toasts"
        :key="toast.id"
        class="toast glass-host"
        :class="`tone-${toast.tone}`"
      >
        <ZGlassSurface material="frosted" depth="frost" />
        <ZIcon
          :name="toast.tone === 'danger' ? 'alert' : toast.tone === 'success' ? 'check' : 'sparkles'"
          :size="16"
        />
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
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow), inset 0 1px 0 var(--glass-hi);
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

.toast-enter-active {
  transition:
    opacity 220ms var(--ease-out),
    transform 420ms var(--spring-bouncy);
}

.toast-leave-active {
  transition:
    opacity 160ms var(--ease-out),
    transform 160ms var(--ease-out);
  position: absolute;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(14px) scale(0.92);
}

.toast-move {
  transition: transform 320ms var(--spring-snappy);
}

/* 移动端底栏在 10px 处，Toast 抬到它上面，避免互相遮挡 */
@media (max-width: 1080px) {
  .viewport {
    bottom: calc(86px + env(safe-area-inset-bottom, 0px));
  }
}

@media (prefers-reduced-motion: reduce) {
  .toast-enter-from,
  .toast-leave-to {
    transform: none;
  }
}
</style>
