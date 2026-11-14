import { computed } from 'vue'
import { useTheme } from '@/utils/theme'

export interface ChartTheme {
  text: string
  muted: string
  grid: string
}

export function resolveChartTheme(isDark: boolean): ChartTheme {
  if (isDark) {
    return {
      text: '#e5e7eb',
      muted: '#9ca3af',
      grid: 'rgba(148, 163, 184, 0.22)'
    }
  }
  return {
    text: '#303133',
    muted: '#606266',
    grid: '#e4e7ed'
  }
}

export function useChartTheme() {
  const { dark: isDark } = useTheme()
  return computed(() => resolveChartTheme(isDark.value))
}
