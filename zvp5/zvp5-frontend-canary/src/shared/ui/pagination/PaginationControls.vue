<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { motion } from 'motion-v'
import { useI18n } from 'vue-i18n'
import ZIcon from '@/shared/ui/ZIcon.vue'

const props = withDefaults(
  defineProps<{
    page: number
    totalPages: number
    disabled?: boolean
  }>(),
  { disabled: false }
)

const emit = defineEmits<{
  'update:page': [value: number]
}>()

const { t } = useI18n()

/* —— 连续页码窗口：始终显示 5（窄屏 3）个连续页码，无省略号 —— */
const pagesEl = ref<HTMLElement | null>(null)
const measuredVisible = ref(5)
const visiblePages = computed(() => Math.max(3, Math.min(5, measuredVisible.value)))

const pages = computed(() => {
  const total = Math.max(0, props.totalPages)
  if (total <= 0) return []
  const count = Math.min(visiblePages.value, total)
  const half = Math.floor(count / 2)
  let start = Math.max(1, props.page - half)
  start = Math.min(start, Math.max(1, total - count + 1))
  const end = Math.min(total, start + count - 1)
  const out: number[] = []
  for (let i = start; i <= end; i++) out.push(i)
  return out
})

const canPrev = computed(() => props.page > 1 && !props.disabled)
const canNext = computed(() => props.page < props.totalPages && !props.disabled)

function go(n: number) {
  if (props.disabled) return
  const total = Math.max(1, props.totalPages)
  const next = Math.max(1, Math.min(total, Math.round(n)))
  if (next !== props.page) emit('update:page', next)
}

/* —— 点击当前页 → 行内输入跳转（无独立输入框/前往按钮）—— */
const editing = ref(false)
const draft = ref('')
const inputRef = ref<HTMLInputElement | null>(null)

function beginEdit() {
  if (props.disabled) return
  draft.value = String(props.page)
  editing.value = true
  nextTick(() => {
    inputRef.value?.focus()
    inputRef.value?.select()
  })
}
function commitEdit() {
  const v = parseInt(draft.value, 10)
  if (Number.isFinite(v)) go(v)
  editing.value = false
}
function cancelEdit() {
  editing.value = false
}
function onPageClick(item: number) {
  if (editing.value) {
    if (item !== props.page) {
      editing.value = false
      go(item)
    }
    return
  }
  if (item === props.page) {
    beginEdit()
    return
  }
  go(item)
}
function onInputKey(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault()
    commitEdit()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    cancelEdit()
  }
}

function onDocPointer(e: PointerEvent) {
  if (!editing.value) return
  if (!pagesEl.value) return
  if (!pagesEl.value.contains(e.target as Node)) commitEdit()
}

/* —— 当前页高亮底板：测量真实位置，Spring 平滑滑动 —— */
const pillX = ref(0)
const pillW = ref(0)
const pillVisible = ref(false)

function measurePill() {
  const host = pagesEl.value
  if (!host) return
  const btn = host.querySelector<HTMLElement>(`[data-page="${props.page}"]`)
  if (!btn) {
    pillVisible.value = false
    return
  }
  pillX.value = btn.offsetLeft
  pillW.value = btn.offsetWidth
  pillVisible.value = true
}

let ro: ResizeObserver | null = null
onMounted(() => {
  document.addEventListener('pointerdown', onDocPointer)
  // 以分页栏整体宽度判断可容纳 5 页还是 3 页，避免用页码自身宽度（循环依赖）
  const host = pagesEl.value?.closest('.adapt') ?? pagesEl.value
  const updateVisible = () => {
    const w = host?.clientWidth ?? window.innerWidth
    measuredVisible.value = w > 0 && w < 560 ? 3 : 5
  }
  updateVisible()
  requestAnimationFrame(() => {
    updateVisible()
    measurePill()
  })
  if (host && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      updateVisible()
      requestAnimationFrame(measurePill)
    })
    ro.observe(host)
  }
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointer)
  ro?.disconnect()
})

watch([() => props.page, () => pages.value.length, visiblePages, editing], () => {
  nextTick(() => requestAnimationFrame(measurePill))
})
</script>

<template>
  <div class="pager">
    <button
      class="nav"
      type="button"
      :disabled="!canPrev"
      :aria-label="t('pagination.prev')"
      @click="go(page - 1)"
    >
      <ZIcon name="chevron-left" :size="16" />
    </button>

    <div ref="pagesEl" class="pages" role="group" :aria-label="t('pagination.label')">
      <motion.span
        v-show="pillVisible"
        class="pill"
        :initial="false"
        :animate="{ x: pillX, width: pillW }"
        :transition="{ type: 'spring', stiffness: 420, damping: 34, mass: 0.6 }"
      />
      <button
        v-for="item in pages"
        :key="item"
        type="button"
        class="pg"
        :class="{ 'is-active': item === page }"
        :data-page="item"
        :aria-current="item === page ? 'page' : undefined"
        @click="onPageClick(item)"
      >
        <template v-if="editing && item === page">
          <input
            ref="inputRef"
            v-model="draft"
            class="jump-input"
            type="text"
            inputmode="numeric"
            :aria-label="t('pagination.jumpTo')"
            @keydown="onInputKey"
            @blur="commitEdit"
            @click.stop
          />
        </template>
        <template v-else>{{ item }}</template>
      </button>
    </div>

    <button
      class="nav"
      type="button"
      :disabled="!canNext"
      :aria-label="t('pagination.next')"
      @click="go(page + 1)"
    >
      <ZIcon name="chevron-right" :size="16" />
    </button>
  </div>
</template>

<style scoped>
.pager {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.nav {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: none;
  border: 0;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--surface-raised) 70%, transparent);
  color: var(--ink-muted);
  cursor: pointer;
  transition:
    background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-out),
    opacity var(--dur-fast) var(--ease-out);
}

.nav:hover:not(:disabled) {
  background: color-mix(in srgb, var(--accent) 12%, var(--surface-raised));
  color: var(--accent-ink);
}

.nav:active:not(:disabled) {
  transform: scale(0.92);
}

.nav:disabled {
  opacity: 0.38;
  cursor: not-allowed;
}

.pages {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 3px;
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--surface-raised) 55%, transparent);
}

.pg {
  position: relative;
  z-index: 1;
  min-width: 34px;
  height: 30px;
  padding: 0 0.55rem;
  border: 0;
  border-radius: calc(var(--radius-pill) - 2px);
  background: transparent;
  color: var(--ink-muted);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  transition:
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}

.pg:hover {
  color: var(--accent-ink);
}

.pg:active {
  transform: scale(0.9);
}

.pg.is-active {
  color: var(--accent-ink);
  font-weight: 600;
}

.pill {
  position: absolute;
  z-index: 0;
  top: 3px;
  left: 0;
  height: 30px;
  border-radius: calc(var(--radius-pill) - 2px);
  background: rgba(255, 252, 244, 0.92);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--glass-hi) 70%, transparent),
    0 6px 16px -8px color-mix(in srgb, var(--accent) 45%, transparent),
    0 2px 6px -2px rgba(0, 0, 0, 0.06);
  will-change: transform, width;
}

:global([data-theme='dark']) .pill {
  background: rgba(74, 63, 48, 0.92);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--glass-hi) 55%, transparent),
    0 6px 18px -8px rgba(0, 0, 0, 0.6);
}

.jump-input {
  width: 2.4rem;
  height: 100%;
  border: 0;
  outline: none;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-align: center;
}
</style>
