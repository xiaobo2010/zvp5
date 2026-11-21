<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    name?: string
    size?: number
    tone?: 'warm' | 'sage' | 'ink'
  }>(),
  { name: '', size: 40, tone: 'warm' }
)

const initial = computed(() => (props.name ? Array.from(props.name)[0] : '·'))
</script>

<template>
  <span
    class="z-avatar"
    :class="`tone-${tone}`"
    :style="{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.max(12, size * 0.4)}px` }"
    :aria-label="name || '用户头像'"
  >
    {{ initial }}
  </span>
</template>

<style scoped>
.z-avatar {
  display: inline-grid;
  place-items: center;
  flex: none;
  border-radius: 50%;
  font-family: var(--font-display);
  font-weight: 600;
  color: #fffdf8;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), var(--shadow-xs);
  user-select: none;
}

.tone-warm {
  background: linear-gradient(140deg, #c58a33, #8c5a12);
}

.tone-sage {
  background: linear-gradient(140deg, #7c957f, #4f6a55);
}

.tone-ink {
  background: linear-gradient(140deg, #4a4338, #24201a);
}
</style>
