import { describe, expect, it } from 'vitest'
import { formatDate, formatHours, greetingKey } from './format'

describe('format helpers', () => {
  it('formats hours without noisy decimals', () => {
    expect(formatHours(12)).toBe('12')
    expect(formatHours(12.34)).toBe('12.3')
    expect(formatHours(null)).toBe('—')
    expect(formatHours(Number.NaN)).toBe('—')
  })

  it('formats dates and tolerates empty values', () => {
    expect(formatDate('2026-11-21T08:00:00')).toBe('2026.11.21')
    expect(formatDate('')).toBe('—')
  })

  it('maps local hours to greeting keys', () => {
    expect(greetingKey(8)).toBe('home.morning')
    expect(greetingKey(14)).toBe('home.afternoon')
    expect(greetingKey(21)).toBe('home.evening')
  })
})
