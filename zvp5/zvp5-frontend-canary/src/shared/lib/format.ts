import dayjs from 'dayjs'

export const TIME_TARGETS = {
  'on-campus': 25,
  'off-campus': 15,
  'social-practice': 18
} as const

export function formatHours(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = dayjs(value)
  return date.isValid() ? date.format('YYYY.MM.DD') : value
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = dayjs(value)
  return date.isValid() ? date.format('YYYY.MM.DD HH:mm') : value
}

export function activityStatusTone(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  if (status === 'effective') return 'success'
  if (status === 'pending') return 'warning'
  if (status === 'refused') return 'danger'
  return 'neutral'
}

export function memberStatusTone(status: string): 'success' | 'warning' | 'danger' | 'neutral' {
  return activityStatusTone(status)
}

export function greetingKey(hour: number): string {
  if (hour < 11) return 'home.morning'
  if (hour < 18) return 'home.afternoon'
  return 'home.evening'
}
