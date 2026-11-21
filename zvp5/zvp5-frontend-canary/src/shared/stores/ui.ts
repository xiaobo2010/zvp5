import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export type ThemeMode = 'light' | 'dark' | 'system'
export type LocaleCode = 'zh-CN' | 'en-US'

const THEME_KEY = 'zvp5.theme'
const LOCALE_KEY = 'zvp5.locale'

export interface Toast {
  id: number
  message: string
  tone: 'neutral' | 'success' | 'danger'
}

function readTheme(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_KEY)
    return raw === 'light' || raw === 'dark' ? raw : 'system'
  } catch {
    return 'system'
  }
}

function readLocale(): LocaleCode {
  try {
    return localStorage.getItem(LOCALE_KEY) === 'en-US' ? 'en-US' : 'zh-CN'
  } catch {
    return 'zh-CN'
  }
}

export function resolveDark(mode: ThemeMode): boolean {
  if (mode === 'dark') return true
  if (mode === 'light') return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export const useUiStore = defineStore('ui', () => {
  const themeMode = ref<ThemeMode>(readTheme())
  const locale = ref<LocaleCode>(readLocale())
  const toasts = ref<Toast[]>([])
  const isDark = computed(() => resolveDark(themeMode.value))

  function applyTheme(): void {
    document.documentElement.dataset.theme = resolveDark(themeMode.value) ? 'dark' : 'light'
  }

  function setTheme(mode: ThemeMode): void {
    themeMode.value = mode
    try {
      localStorage.setItem(THEME_KEY, mode)
    } catch {
      // ignore
    }
    applyTheme()
  }

  function toggleTheme(): void {
    setTheme(resolveDark(themeMode.value) ? 'light' : 'dark')
  }

  function setLocale(next: LocaleCode): void {
    locale.value = next
    try {
      localStorage.setItem(LOCALE_KEY, next)
    } catch {
      // ignore
    }
  }

  function notify(message: string, tone: Toast['tone'] = 'neutral'): void {
    const id = Date.now() + Math.random()
    toasts.value = [...toasts.value, { id, message, tone }]
    window.setTimeout(() => {
      toasts.value = toasts.value.filter((toast) => toast.id !== id)
    }, 4200)
  }

  return {
    themeMode,
    locale,
    toasts,
    isDark,
    applyTheme,
    setTheme,
    toggleTheme,
    setLocale,
    notify
  }
})
