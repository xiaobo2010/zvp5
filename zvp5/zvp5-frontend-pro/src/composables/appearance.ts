import { computed, onMounted, onUnmounted, ref } from 'vue'

export const palettes = [
  { id: 'dawn', label: '晨曦', description: '蜜桃与雾蓝' },
  { id: 'day', label: '晴昼', description: '天蓝与浅绿' },
  { id: 'dusk', label: '暮霞', description: '暖杏与淡紫' },
  { id: 'night', label: '静夜', description: '月蓝与柔紫' },
] as const

export type Palette = typeof palettes[number]['id']
export type PalettePreference = Palette | 'auto'

const storageKey = 'zvms-pro-appearance'

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
  const localPhase = ref(phaseAt(new Date().getHours()))
  let timer: number | undefined

  function syncLight() {
    window.clearTimeout(timer)
    if (document.hidden) return

    const now = new Date()
    localPhase.value = phaseAt(now.getHours())
    const withinHour = (now.getMinutes() * 60 + now.getSeconds()) * 1000 + now.getMilliseconds()
    timer = window.setTimeout(syncLight, 3_600_000 - withinHour)
  }

  function syncPreference(event: StorageEvent) {
    if (event.key === storageKey || event.key === null) preference.value = readPreference()
  }

  onMounted(() => {
    syncLight()
    document.addEventListener('visibilitychange', syncLight)
    window.addEventListener('pageshow', syncLight)
    window.addEventListener('storage', syncPreference)
  })

  onUnmounted(() => {
    window.clearTimeout(timer)
    document.removeEventListener('visibilitychange', syncLight)
    window.removeEventListener('pageshow', syncLight)
    window.removeEventListener('storage', syncPreference)
  })

  return computed(() => preference.value === 'auto' ? localPhase.value : preference.value)
}
