/**
 * 全站深浅色的唯一状态源。
 *
 * 之前每个组件各自 `useDark()`，在同一个标签页里互不通知：顶栏点了切换，
 * 设置页的开关还是旧值；而且 useDark 与 data-theme 双轨，容易只改到
 * html.dark 却没改 data-theme（v2 令牌读的是 data-theme）。
 *
 * 这里用一个模块级 preference 收口：三个存储值 light / dark / auto
 * 沿用旧的 localStorage 键 `vueuse-color-scheme`，用户既有选择不会丢；
 * auto 时跟随系统，并在系统主题变化时实时响应。
 */
import { computed, ref, watch } from 'vue'

export type ThemePreference = 'light' | 'dark' | 'auto'

const STORAGE_KEY = 'vueuse-color-scheme'

const media =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

function readPreference(): ThemePreference {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'auto') {
      return raw
    }
  } catch {
    // 隐私模式下 localStorage 可能抛错，退回跟随系统
  }
  return 'auto'
}

const preference = ref<ThemePreference>(readPreference())
const systemDark = ref(media?.matches ?? false)

media?.addEventListener('change', (event: MediaQueryListEvent) => {
  systemDark.value = event.matches
})

const dark = computed(
  () => preference.value === 'auto' ? systemDark.value : preference.value === 'dark'
)

function apply() {
  if (typeof document === 'undefined') {
    return
  }
  const root = document.documentElement
  // html.dark 给 Element Plus 用，data-theme 给 v2 令牌用，两者必须同时写。
  root.classList.toggle('dark', dark.value)
  root.dataset.theme = dark.value ? 'dark' : 'light'
}

watch(
  [dark, preference],
  () => {
    try {
      localStorage.setItem(STORAGE_KEY, preference.value)
    } catch {
      // 存不进去也不影响本次会话的显示
    }
    apply()
  },
  { immediate: true, flush: 'sync' }
)

export function setTheme(next: ThemePreference) {
  preference.value = next
}

export function toggleTheme() {
  preference.value = dark.value ? 'light' : 'dark'
}

export function useTheme() {
  return { dark, preference, setTheme, toggleTheme }
}
