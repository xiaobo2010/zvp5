import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useSessionStore } from '@/shared/stores/session'

export type PageColumns = 1 | 2

export interface AdaptivePaginationOptions {
  /** 列表唯一标识，用于隔离不同业务的偏好与缓存 key */
  listId: string
  /** 列表容器；自动模式用它读取顶部位置与可用宽度 */
  container?: Ref<HTMLElement | null>
  /** 条目选择器，用于实测单条高度（不同页面卡片高度不同） */
  itemSelector?: string
  defaultPageSize?: number
  pageSizeOptions?: number[]
  min?: number
  max?: number
  /** 网格间距（px） */
  gap?: number
  /** 列表底部需要为分页栏与页脚预留的高度（px） */
  reserve?: number
  fallbackItemHeight?: number
  allowAuto?: boolean
  allowColumns?: boolean
  defaultColumns?: PageColumns
}

interface StoredPrefs {
  auto?: boolean
  pageSize?: number
  columns?: PageColumns
}

const DEFAULT_SIZES = [6, 10, 20, 40, 60]

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

/**
 * 自适应分页：根据「列表容器顶部到视口底部」的真实可用空间、实测条目高度和当前列数，
 * 计算每页能完整放下的条目数量。用户手动选择后不再被窗口变化覆盖，切换回自动模式时恢复。
 */
export function useAdaptivePagination(options: AdaptivePaginationOptions) {
  const session = useSessionStore()
  const min = options.min ?? 1
  const max = options.max ?? 120
  const gap = options.gap ?? 16
  const reserve = options.reserve ?? 96
  const fallbackItemHeight = options.fallbackItemHeight ?? 220
  const allowAuto = options.allowAuto !== false
  const allowColumns = options.allowColumns === true

  const storageKey = computed(
    () => `zvp5.pager.${session.profile?._id ?? 'anon'}.${options.listId}`
  )

  function readPrefs(): StoredPrefs {
    try {
      const raw = localStorage.getItem(storageKey.value)
      if (!raw) return {}
      const parsed = JSON.parse(raw) as StoredPrefs
      return parsed && typeof parsed === 'object' ? parsed : {}
    } catch {
      return {}
    }
  }

  const stored = readPrefs()
  const initialSize = clamp(stored.pageSize ?? options.defaultPageSize ?? 12, min, max)
  const initialColumns: PageColumns =
    allowColumns && stored.columns === 2 ? 2 : options.defaultColumns === 2 && allowColumns ? 2 : 1

  const auto = ref(allowAuto ? stored.auto ?? true : false)
  const manualSize = ref(initialSize)
  const columns = ref<PageColumns>(initialColumns)
  /** 实际参与计算的列数：容器过窄时即使选了双列也降级为单列 */
  const effectiveColumns = ref<PageColumns>(initialColumns)
  const size = ref(initialSize)

  let observer: ResizeObserver | null = null
  let frame = 0
  let timer: number | undefined
  let observedItem: HTMLElement | null = null

  function persist() {
    try {
      localStorage.setItem(
        storageKey.value,
        JSON.stringify({ auto: auto.value, pageSize: manualSize.value, columns: columns.value })
      )
    } catch {
      // 隐私模式下 localStorage 可能不可写，忽略即可
    }
  }

  function measure() {
    const el = options.container?.value
    if (!el) return
    const rect = el.getBoundingClientRect()
    if (!rect.width) return

    const item = el.querySelector<HTMLElement>(options.itemSelector ?? '')
    const itemHeight =
      item && item.offsetHeight > 0 ? item.offsetHeight : fallbackItemHeight
    const docTop = rect.top + window.scrollY
    const available = Math.max(itemHeight, window.innerHeight - docTop - reserve)
    const rows = Math.max(1, Math.floor((available + gap) / (itemHeight + gap)))
    const nextColumns: PageColumns = rect.width < 760 ? 1 : columns.value

    effectiveColumns.value = nextColumns
    const next = clamp(rows * nextColumns, min, max)
    if (auto.value && next !== size.value) size.value = next
  }

  /** rAF 合并 + 尾部防抖：窗口拖动时不会每帧触发数据请求 */
  function recalc(immediate = false) {
    if (timer) window.clearTimeout(timer)
    const run = () => {
      if (frame) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        frame = 0
        measure()
      })
    }
    timer = window.setTimeout(run, immediate ? 0 : 120)
  }

  function observeItem() {
    const el = options.container?.value
    const item = el?.querySelector<HTMLElement>(options.itemSelector ?? '')
    if (!item || item === observedItem || !observer) return
    if (observedItem) observer.unobserve(observedItem)
    observedItem = item
    observer.observe(item)
  }

  function setAuto(value: boolean) {
    if (!allowAuto) return
    auto.value = value
    persist()
    if (value) recalc(true)
  }

  function setPageSize(value: number) {
    const next = clamp(Math.round(value) || min, min, max)
    auto.value = false
    manualSize.value = next
    size.value = next
    persist()
  }

  function setColumns(value: PageColumns) {
    if (!allowColumns) return
    columns.value = value
    persist()
    recalc(true)
  }

  const pageSizeOptions = computed(() => {
    const list = [...(options.pageSizeOptions ?? DEFAULT_SIZES)].filter(
      (item) => item >= min && item <= max
    )
    if (!list.includes(size.value)) list.push(size.value)
    return [...new Set(list)].sort((a, b) => a - b)
  })

  onMounted(async () => {
    await nextTick()
    if (options.container?.value) {
      observer = new ResizeObserver(() => {
        observeItem()
        recalc()
      })
      observer.observe(options.container.value)
      observeItem()
    }
    window.addEventListener('resize', onWindowResize)
    recalc(true)
  })

  function onWindowResize() {
    recalc()
  }

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
    observedItem = null
    window.removeEventListener('resize', onWindowResize)
    if (timer) window.clearTimeout(timer)
    if (frame) cancelAnimationFrame(frame)
  })

  watch(
    () => session.profile?._id,
    () => {
      const next = readPrefs()
      auto.value = allowAuto ? next.auto ?? true : false
      manualSize.value = clamp(next.pageSize ?? options.defaultPageSize ?? 12, min, max)
      columns.value = allowColumns && next.columns === 2 ? 2 : 1
      size.value = manualSize.value
      recalc(true)
    }
  )

  return {
    size,
    auto,
    columns,
    effectiveColumns,
    pageSizeOptions,
    setAuto,
    setPageSize,
    setColumns,
    recalc
  }
}
