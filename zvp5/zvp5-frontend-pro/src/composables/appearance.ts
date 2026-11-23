import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchServerHour } from '@/api/server-time'

export const palettes = [
  { id: 'dawn', label: '晨曦', description: '蜜桃与雾蓝' },
  { id: 'day', label: '晴昼', description: '天蓝与浅绿' },
  { id: 'dusk', label: '暮霞', description: '暖杏与淡紫' },
  { id: 'night', label: '静夜', description: '月蓝与柔紫' },
] as const

export type Palette = typeof palettes[number]['id']
export type PalettePreference = Palette | 'auto'

const storageKey = 'zvms-pro-appearance'

/** Refresh cadence while the tab stays visible (the contract is ≤ 10 min). */
const SERVER_TIME_REFRESH_MS = 10 * 60 * 1000

function normalizePreference(value: string | null): PalettePreference {
  return palettes.find((palette) => palette.id === value)?.id ?? 'auto'
}

function readPreference(): PalettePreference {
  try {
    return normalizePreference(localStorage.getItem(storageKey))
  } catch {
    return 'auto'
  }
}

const preference = ref<PalettePreference>(readPreference())
export const palettePreference = computed(() => preference.value)

export function setPalettePreference(value: PalettePreference): boolean {
  preference.value = value
  try {
    localStorage.setItem(storageKey, value)
    return true
  } catch {
    return false
  }
}

function phaseAt(hour: number): Palette {
  if (hour < 6 || hour >= 21) return 'night'
  if (hour < 10) return 'dawn'
  if (hour < 17) return 'day'
  return 'dusk'
}

export function useAmbientPalette() {
  // Local hour is only the first paint / offline fallback. The server's `hour`
  // is authoritative because a tablet clock and timezone cannot be trusted.
  const phase = ref<Palette>(phaseAt(new Date().getHours()))
  let timer: number | undefined
  let inflight: AbortController | null = null
  let serverHour: number | null = null
  let disposed = false

  function schedule() {
    window.clearTimeout(timer)
    if (disposed || document.hidden) return
    timer = window.setTimeout(() => {
      void syncLight()
    }, SERVER_TIME_REFRESH_MS)
  }

  async function syncLight() {
    window.clearTimeout(timer)
    if (disposed || document.hidden) return

    const localHour = new Date().getHours()
    // Until a server response lands, keep the local clock as the fallback.
    if (serverHour === null) phase.value = phaseAt(localHour)

    // A new visibility/pageshow event wins over an in-flight read.
    inflight?.abort()
    const controller = new AbortController()
    inflight = controller
    const timeout = window.setTimeout(() => controller.abort(), 6000)

    try {
      const hour = await fetchServerHour(controller.signal)
      if (controller.signal.aborted || disposed) return
      if (hour !== null) {
        serverHour = hour
        phase.value = phaseAt(hour)
      } else if (serverHour === null) {
        phase.value = phaseAt(localHour)
      }
    } catch {
      // Network failure / abort: keep the last server phase if we had one,
      // otherwise use the local clock. The next tick retries.
      if (!controller.signal.aborted && !disposed && serverHour === null) {
        phase.value = phaseAt(localHour)
      }
    } finally {
      window.clearTimeout(timeout)
      if (inflight === controller) inflight = null
      schedule()
    }
  }

  function syncPreference(event: StorageEvent) {
    if (event.key === storageKey || event.key === null) preference.value = readPreference()
  }

  function onVisibilityChange() {
    if (!document.hidden) void syncLight()
  }

  function onPageShow() {
    void syncLight()
  }

  onMounted(() => {
    void syncLight()
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pageshow', onPageShow)
    window.addEventListener('storage', syncPreference)
  })

  onUnmounted(() => {
    disposed = true
    window.clearTimeout(timer)
    inflight?.abort()
    document.removeEventListener('visibilitychange', onVisibilityChange)
    window.removeEventListener('pageshow', onPageShow)
    window.removeEventListener('storage', syncPreference)
  })

  return computed(() => (preference.value === 'auto' ? phase.value : preference.value))
}
