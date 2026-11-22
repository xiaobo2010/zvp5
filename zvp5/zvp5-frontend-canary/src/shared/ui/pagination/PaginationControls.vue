<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { motion } from 'motion-v'
import { useI18n } from 'vue-i18n'
import ZButton from '@/shared/ui/ZButton.vue'
import { springs } from '@/motion/presets'

const props = withDefaults(
  defineProps<{
    page: number
    totalPages: number
    disabled?: boolean
  }>(),
  { disabled: false }
)

const emit = defineEmits<{ 'update:page': [page: number] }>()
const { t } = useI18n()

const pagesEl = ref<HTMLElement | null>(null)
const pill = ref({ x: 0, width: 0, ready: false })
const jump = ref('')
let observer: ResizeObserver | null = null

/** 页码窗口：首尾常驻，当前页左右各留一位，其余用省略号收敛 */
const pages = computed<(number | 'gap')[]>(() => {
  const total = props.totalPages
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1)

  const set = new Set<number>([1, total, props.page - 1, props.page, props.page + 1])
  if (props.page <= 3) [2, 3, 4].forEach((item) => set.add(item))
  if (props.page >= total - 2) [total - 3, total - 2, total - 1].forEach((item) => set.add(item))

  const sorted = [...set].filter((item) => item >= 1 && item <= total).sort((a, b) => a - b)
  const result: (number | 'gap')[] = []
  sorted.forEach((item, index) => {
    if (index > 0 && item - (sorted[index - 1] ?? item) > 1) result.push('gap')
    result.push(item)
  })
  return result
})

function go(next: number) {
  const value = Math.max(1, Math.min(props.totalPages, Math.round(next) || 1))
  if (value !== props.page) emit('update:page', value)
}

function submitJump() {
  const value = Number.parseInt(jump.value, 10)
  if (!Number.isFinite(value)) return
  go(value)
  jump.value = ''
}

async function measurePill() {
  await nextTick()
  const container = pagesEl.value
  const active = container?.querySelector<HTMLElement>(`[data-page="${props.page}"]`)
  if (!container || !active) {
    pill.value = { ...pill.value, ready: false }
    return
  }
  pill.value = { x: active.offsetLeft, width: active.offsetWidth, ready: true }
}

onMounted(() => {
  void measurePill()
  if (pagesEl.value) {
    observer = new ResizeObserver(() => void measurePill())
    observer.observe(pagesEl.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

watch(() => [props.page, props.totalPages], () => void measurePill())
</script>

<template>
  <nav class="controls" :aria-label="t('pagination.label')">
    <ZButton
      variant="ghost"
      size="sm"
      icon="chevron-left"
      :disabled="disabled || page <= 1"
      @click="go(page - 1)"
    >
      {{ t('pagination.prev') }}
    </ZButton>

    <div ref="pagesEl" class="pages">
      <motion.span
        class="pill"
        aria-hidden="true"
        :initial="false"
        :animate="{ x: pill.x, width: pill.width, opacity: pill.ready ? 1 : 0 }"
        :transition="springs.snappy"
      />
      <template v-for="(item, index) in pages" :key="`${item}-${index}`">
        <span v-if="item === 'gap'" class="gap" aria-hidden="true">…</span>
        <button
          v-else
          type="button"
          class="pg num"
          :class="{ 'is-active': item === page }"
          :data-page="item"
          :aria-current="item === page ? 'page' : undefined"
          :aria-label="t('pagination.goPage', { page: item })"
          @click="go(item)"
        >
          {{ item }}
        </button>
      </template>
    </div>

    <ZButton
      variant="ghost"
      size="sm"
      icon-right="chevron-right"
      :disabled="disabled || page >= totalPages"
      @click="go(page + 1)"
    >
      {{ t('pagination.next') }}
    </ZButton>

    <form class="jump" @submit.prevent="submitJump">
      <label class="jump-label" :for="`jump-${page}`">{{ t('pagination.jumpTo') }}</label>
      <input
        :id="`jump-${page}`"
        v-model="jump"
        class="jump-input num"
        type="number"
        inputmode="numeric"
        min="1"
        :max="totalPages"
        :placeholder="String(page)"
        :aria-label="t('pagination.jumpTo')"
      />
      <button type="submit" class="jump-go">{{ t('pagination.jump') }}</button>
    </form>
  </nav>
</template>

<style scoped>
.controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.pages {
  position: relative;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}

.pill {
  position: absolute;
  top: 3px;
  left: 0;
  height: calc(100% - 6px);
  border-radius: var(--radius-pill);
  background: rgba(255, 252, 244, 0.9);
  border: 1px solid color-mix(in srgb, var(--glass-border) 80%, transparent);
  box-shadow:
    inset 0 1px 0 var(--glass-hi),
    0 4px 12px -8px color-mix(in srgb, var(--accent) 60%, transparent);
  pointer-events: none;
}

[data-theme='dark'] .pill {
  background: rgba(74, 63, 48, 0.92);
  border-color: rgba(255, 235, 200, 0.16);
  box-shadow:
    inset 0 1px 0 rgba(255, 244, 224, 0.12),
    0 4px 14px -8px rgba(0, 0, 0, 0.6);
}

.pg,
.gap {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  min-width: 30px;
  height: 28px;
  padding: 0 0.35rem;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  font-size: var(--text-xs);
  color: var(--ink-muted);
  transition: color var(--dur-fast) var(--ease-out);
}

.pg:hover {
  color: var(--ink);
}

.pg.is-active {
  color: var(--ink);
  font-weight: 600;
}

.pg:active {
  transform: scale(0.94);
}

.gap {
  color: var(--ink-faint);
  min-width: 18px;
}

.jump {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-left: var(--space-2);
}

.jump-label {
  font-size: var(--text-2xs);
  color: var(--ink-faint);
  white-space: nowrap;
}

.jump-input {
  width: 3rem;
  height: 28px;
  padding: 0 0.4rem;
  border: 1px solid color-mix(in srgb, var(--line) 80%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--surface) 60%, transparent);
  color: var(--ink);
  font-size: var(--text-xs);
  text-align: center;
  outline: none;
  transition:
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
}

.jump-input:focus {
  border-color: color-mix(in srgb, var(--accent) 60%, var(--line));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 16%, transparent);
}

.jump-go {
  height: 28px;
  padding: 0 0.55rem;
  border-radius: var(--radius-sm);
  border: 1px solid color-mix(in srgb, var(--glass-border) 80%, transparent);
  background: color-mix(in srgb, var(--glass-raised) 70%, transparent);
  color: var(--ink-soft);
  font-size: var(--text-2xs);
  white-space: nowrap;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--spring-snappy);
}

.jump-go:hover {
  background: color-mix(in srgb, var(--glass-raised) 95%, transparent);
}

.jump-go:active {
  transform: scale(0.95);
}

@media (max-width: 900px) {
  .jump {
    display: none;
  }
}

@media (max-width: 640px) {
  .pages {
    display: none;
  }
}
</style>
