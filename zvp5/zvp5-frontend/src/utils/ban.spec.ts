import { describe, expect, it } from 'vitest'
import { formatBanUntil, readBanInfo } from './ban'

/** AxiosHeaders 风格:只有 get 方法,普通属性访问取不到值。 */
function axiosHeaders(entries: Record<string, string>) {
  return {
    get(name: string) {
      return entries[name.toLowerCase()]
    }
  }
}

describe('readBanInfo', () => {
  it('reads AxiosHeaders-style headers', () => {
    const info = readBanInfo(
      axiosHeaders({ 'x-ban-reason': 'cheating', 'x-ban-until': '1700000000' })
    )
    expect(info).toEqual({ reason: 'cheating', until: 1_700_000_000_000 })
  })

  it('reads plain object headers', () => {
    const info = readBanInfo({ 'x-ban-reason': 'spam', 'x-ban-until': '1700000001' })
    expect(info).toEqual({ reason: 'spam', until: 1_700_000_001_000 })
  })

  it('takes the first value of array headers', () => {
    const info = readBanInfo({ 'x-ban-reason': ['array-reason'], 'x-ban-until': ['1700000002'] })
    expect(info).toEqual({ reason: 'array-reason', until: 1_700_000_002_000 })
  })

  it('decodes URL-encoded Chinese reasons', () => {
    const reason = '违规使用平板'
    const info = readBanInfo({ 'x-ban-reason': encodeURIComponent(reason), 'x-ban-until': '' })
    expect(info.reason).toBe(reason)
  })

  it('treats an empty until as permanent (0)', () => {
    const info = readBanInfo({ 'x-ban-reason': 'x', 'x-ban-until': '' })
    expect(info.until).toBe(0)
  })

  it('treats an invalid until as permanent (0)', () => {
    expect(readBanInfo({ 'x-ban-until': 'not-a-number' }).until).toBe(0)
    expect(readBanInfo({ 'x-ban-until': '-5' }).until).toBe(0)
    expect(readBanInfo({ 'x-ban-until': '0' }).until).toBe(0)
  })

  it('falls back to the raw string on malformed percent-encoding', () => {
    const info = readBanInfo({ 'x-ban-reason': '%E4%B8' })
    expect(info.reason).toBe('%E4%B8')
  })

  it('returns empty reason and permanent until when headers are absent', () => {
    expect(readBanInfo(undefined)).toEqual({ reason: '', until: 0 })
    expect(readBanInfo({})).toEqual({ reason: '', until: 0 })
  })
})

describe('formatBanUntil', () => {
  it('delegates to the formatter for timed bans', () => {
    expect(formatBanUntil(1_700_000_000_000, (ms) => `at ${ms}`)).toBe('at 1700000000000')
  })

  it('returns an empty string for permanent bans so callers pick the wording', () => {
    expect(formatBanUntil(0, () => 'never')).toBe('')
  })
})
