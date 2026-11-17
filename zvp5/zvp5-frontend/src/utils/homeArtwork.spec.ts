import { describe, expect, it } from 'vitest'
import {
  assignHomeArtwork,
  buildRevealRects,
  calculateHomeProgress,
  composeChannelReveal,
  HOME_CATEGORY_CHANNEL,
  type RevealRect
} from './homeArtwork'

function rasterize(rects: RevealRect[], width: number, height: number): Uint8Array {
  const pixels = new Uint8Array(width * height)

  for (const rect of rects) {
    expect(Number.isInteger(rect.x)).toBe(true)
    expect(Number.isInteger(rect.y)).toBe(true)
    expect(Number.isInteger(rect.width)).toBe(true)
    expect(Number.isInteger(rect.height)).toBe(true)
    expect(rect.width).toBeGreaterThan(0)
    expect(rect.height).toBeGreaterThan(0)
    expect(rect.x).toBeGreaterThanOrEqual(0)
    expect(rect.y).toBeGreaterThanOrEqual(0)
    expect(rect.x + rect.width).toBeLessThanOrEqual(width)
    expect(rect.y + rect.height).toBeLessThanOrEqual(height)

    for (let y = rect.y; y < rect.y + rect.height; y++) {
      for (let x = rect.x; x < rect.x + rect.width; x++) {
        pixels[y * width + x]++
      }
    }
  }

  return pixels
}

function countCovered(pixels: Uint8Array): { area: number; overlaps: number } {
  let area = 0
  let overlaps = 0
  for (const count of pixels) {
    if (count > 0) area++
    if (count > 1) overlaps += count - 1
  }
  return { area, overlaps }
}

describe('calculateHomeProgress', () => {
  it('uses the 25/15/18 targets, applies reciprocal bonuses, and totals raw hours once', () => {
    const progress = calculateHomeProgress({
      onCampus: 26.5,
      offCampus: 32,
      socialPractice: 6.257
    })
    const [on, off, social] = progress.categories

    expect(progress.categories.map(({ key, label, target }) => ({ key, label, target }))).toEqual([
      { key: 'on', label: '校内义工', target: 25 },
      { key: 'off', label: '校外义工', target: 15 },
      { key: 'social', label: '社会实践', target: 18 }
    ])
    expect(on).toMatchObject({
      raw: 26.5,
      bonus: 9,
      effective: 35.5,
      percent: 142,
      ratio: 1,
      gap: 10.5
    })
    expect(off).toMatchObject({
      raw: 32,
      bonus: 1,
      effective: 33,
      percent: 220,
      ratio: 1,
      gap: 18
    })
    expect(social).toMatchObject({
      raw: 6.257,
      bonus: 0,
      effective: 6.26,
      percent: 34.78,
      gap: -11.74
    })
    expect(social.ratio).toBeCloseTo(6.26 / 18, 12)
    // Bonus hours are derived, not inputs: the total remains 26.5 + 32 + 6.257.
    expect(progress.total).toBe(64.76)
    expect(progress.hasBonus).toBe(true)
  })

  it('starts each bonus only above its strict threshold', () => {
    const atThreshold = calculateHomeProgress({ onCampus: 25, offCampus: 15, socialPractice: 0 })
    const roundedToZero = calculateHomeProgress({
      onCampus: 25.01,
      offCampus: 15.01,
      socialPractice: 0
    })
    const aboveThreshold = calculateHomeProgress({
      onCampus: 32,
      offCampus: 32,
      socialPractice: 0
    })

    expect(atThreshold.categories.map((category) => category.bonus)).toEqual([0, 0, 0])
    expect(atThreshold.hasBonus).toBe(false)
    expect(roundedToZero.categories.map((category) => category.bonus)).toEqual([0, 0, 0])
    expect(roundedToZero.hasBonus).toBe(false)
    expect(aboveThreshold.categories.map((category) => category.bonus)).toEqual([9, 2, 0])
    expect(aboveThreshold.categories.map((category) => category.effective)).toEqual([41, 34, 0])
  })

  it('sanitizes negative and non-finite hours to zero', () => {
    const progress = calculateHomeProgress({
      onCampus: -4,
      offCampus: Number.NaN,
      socialPractice: Number.POSITIVE_INFINITY
    })

    expect(progress.categories).toEqual([
      expect.objectContaining({
        key: 'on',
        raw: 0,
        effective: 0,
        bonus: 0,
        percent: 0,
        ratio: 0,
        gap: -25
      }),
      expect.objectContaining({
        key: 'off',
        raw: 0,
        effective: 0,
        bonus: 0,
        percent: 0,
        ratio: 0,
        gap: -15
      }),
      expect.objectContaining({
        key: 'social',
        raw: 0,
        effective: 0,
        bonus: 0,
        percent: 0,
        ratio: 0,
        gap: -18
      })
    ])
    expect(progress.total).toBe(0)
    expect(progress.hasBonus).toBe(false)
  })

  it('keeps percentages above 100 while clamping visual ratios to one', () => {
    const progress = calculateHomeProgress({ onCampus: 250, offCampus: 15, socialPractice: 101 })

    expect(progress.categories[0]).toMatchObject({ percent: 1000, ratio: 1 })
    expect(progress.categories[1]).toMatchObject({
      bonus: 75,
      effective: 90,
      percent: 600,
      ratio: 1
    })
    expect(progress.categories[2].percent).toBeGreaterThan(100)
    expect(progress.categories[2].ratio).toBe(1)
    expect(progress.total).toBe(366)
  })
})

describe('assignHomeArtwork', () => {
  it('returns null for an empty user key', () => {
    expect(assignHomeArtwork('')).toBeNull()
    expect(assignHomeArtwork('   ')).toBeNull()
  })

  it('keeps the v1 artwork order, salt, and seed stable for a user', () => {
    const pinnedAssignments = [
      ['user-a', 'met-436535', 3716808690],
      ['1001', 'met-436532', 1876581878],
      ['alpha', 'met-436528', 1142619239]
    ] as const

    const actual = pinnedAssignments.map(([key]) => {
      const first = assignHomeArtwork(key)
      const afterReload = assignHomeArtwork(key)
      expect(afterReload).toEqual(first)
      expect(first).not.toBeNull()
      return [key, first!.artwork.id, first!.seed] as const
    })

    expect(actual).toEqual(pinnedAssignments)
  })

  it('normalizes outer whitespace without sharing an assignment across users', () => {
    expect(assignHomeArtwork(' user-a ')).toEqual(assignHomeArtwork('user-a'))
    expect(assignHomeArtwork('alpha')?.seed).not.toBe(assignHomeArtwork('beta')?.seed)
  })
})

describe('buildRevealRects', () => {
  it('covers exactly the floored pixel budget without overlaps, including a partial tile', () => {
    const width = 37.8
    const height = 23.9
    const ratio = 0.617
    const tileSize = 6.7
    const integerWidth = Math.floor(width)
    const integerHeight = Math.floor(height)
    const rects = buildRevealRects(width, height, ratio, 0x12345678, tileSize)
    const coverage = rasterize(rects, integerWidth, integerHeight)
    const { area, overlaps } = countCovered(coverage)

    expect(area).toBe(Math.floor(ratio * integerWidth * integerHeight))
    expect(overlaps).toBe(0)
  })

  it('fills odd-sized canvases without crossing bounds or leaving edge pixels uncovered', () => {
    const width = 35
    const height = 23
    const rects = buildRevealRects(width, height, 1, 42, 6)
    const coverage = rasterize(rects, width, height)
    const { area, overlaps } = countCovered(coverage)

    expect(rects.some((rect) => rect.x + rect.width === width && rect.width < 6)).toBe(true)
    expect(rects.some((rect) => rect.y + rect.height === height && rect.height < 6)).toBe(true)
    expect(area).toBe(width * height)
    expect(overlaps).toBe(0)
  })

  it('is deterministic and only adds pixels as progress grows with the same seed', () => {
    const width = 41
    const height = 29
    const seed = 0xdecafbad
    const tileSize = 7
    let previous: Uint8Array = new Uint8Array(width * height)

    for (const ratio of [0, 0.01, 0.125, 0.333, 0.5, 0.777, 0.99, 1]) {
      const first = buildRevealRects(width, height, ratio, seed, tileSize)
      const repeated = buildRevealRects(width, height, ratio, seed, tileSize)
      const coverage = rasterize(first, width, height)
      const { area, overlaps } = countCovered(coverage)
      let removed = 0

      expect(repeated).toEqual(first)
      expect(area).toBe(Math.floor(ratio * width * height))
      expect(overlaps).toBe(0)
      for (let i = 0; i < coverage.length; i++) {
        if (previous[i] && !coverage[i]) removed++
      }
      expect(removed).toBe(0)
      previous = coverage
    }
  })

  it('clamps ratios and rejects invalid canvas or tile dimensions', () => {
    expect(buildRevealRects(10, 10, -1, 1, 4)).toEqual([])
    expect(buildRevealRects(10, 10, Number.NaN, 1, 4)).toEqual([])
    expect(buildRevealRects(10, 10, 0.5, 1, 0)).toEqual([])
    expect(buildRevealRects(0, 10, 0.5, 1, 4)).toEqual([])
    expect(buildRevealRects(10, Number.POSITIVE_INFINITY, 0.5, 1, 4)).toEqual([])

    const full = rasterize(buildRevealRects(6, 5, 2, 1, 4), 6, 5)
    expect(countCovered(full)).toEqual({ area: 30, overlaps: 0 })
  })
})

describe('composeChannelReveal', () => {
  function sourcePixels(width: number, height: number): Uint8ClampedArray {
    const pixels = new Uint8ClampedArray(width * height * 4)
    for (let i = 0; i < pixels.length; i += 4) {
      pixels[i] = 10
      pixels[i + 1] = 20
      pixels[i + 2] = 30
      pixels[i + 3] = 255
    }
    return pixels
  }

  function countChannel(pixels: Uint8ClampedArray, channel: number, value: number): number {
    let count = 0
    for (let i = channel; i < pixels.length; i += 4) if (pixels[i] === value) count++
    return count
  }

  function composeWithRects(
    source: Uint8ClampedArray,
    width: number,
    height: number,
    categories: Array<{ key: 'on' | 'off' | 'social'; ratio: number }>,
    seed: number
  ): Uint8ClampedArray {
    const pixels = new Uint8ClampedArray(width * height * 4)
    pixels.fill(255)
    for (const category of categories) {
      const channel = HOME_CATEGORY_CHANNEL[category.key]
      if (!Number.isFinite(category.ratio) || category.ratio <= 0) continue
      for (const rect of buildRevealRects(width, height, category.ratio, seed)) {
        for (let y = rect.y; y < rect.y + rect.height; y++) {
          for (let x = rect.x; x < rect.x + rect.width; x++) {
            const offset = (y * width + x) * 4
            const alpha = source[offset + 3]! / 255
            pixels[offset + channel] = Math.round(
              source[offset + channel]! * alpha + 255 * (1 - alpha)
            )
          }
        }
      }
    }
    return pixels
  }

  it('starts from white and lights the floored percentage of total pixels per channel', () => {
    const width = 7
    const height = 5
    const total = width * height
    const pixels = composeChannelReveal(
      sourcePixels(width, height),
      width,
      height,
      [
        { key: 'on', ratio: 0.5 },
        { key: 'off', ratio: 0.25 },
        { key: 'social', ratio: 0.8 }
      ],
      42
    )

    expect(pixels).toHaveLength(total * 4)
    expect(countChannel(pixels, 0, 10)).toBe(Math.floor(0.5 * total))
    expect(countChannel(pixels, 1, 20)).toBe(Math.floor(0.25 * total))
    expect(countChannel(pixels, 2, 30)).toBe(Math.floor(0.8 * total))
    expect(countChannel(pixels, 0, 255)).toBe(total - Math.floor(0.5 * total))
    expect(countChannel(pixels, 1, 255)).toBe(total - Math.floor(0.25 * total))
    expect(countChannel(pixels, 2, 255)).toBe(total - Math.floor(0.8 * total))
    for (let i = 3; i < pixels.length; i += 4) expect(pixels[i]).toBe(255)
  })

  it('maps categories by key and is independent of input order', () => {
    const width = 19
    const height = 13
    const source = sourcePixels(width, height)
    const first = composeChannelReveal(
      source,
      width,
      height,
      [
        { key: 'social', ratio: 0.2 },
        { key: 'on', ratio: 0.55 },
        { key: 'off', ratio: 0.7 }
      ],
      7
    )
    const reordered = composeChannelReveal(
      source,
      width,
      height,
      [
        { key: 'on', ratio: 0.55 },
        { key: 'off', ratio: 0.7 },
        { key: 'social', ratio: 0.2 }
      ],
      7
    )

    expect(reordered).toEqual(first)
  })

  it('matches the previous rectangle composition pixel for pixel', () => {
    const width = 37
    const height = 29
    const source = new Uint8ClampedArray(width * height * 4)
    for (let i = 0; i < source.length; i += 4) {
      source[i] = (i * 13) % 256
      source[i + 1] = (i * 29 + 7) % 256
      source[i + 2] = (i * 47 + 11) % 256
      source[i + 3] = i % 9 === 0 ? 128 : 255
    }
    const categories = [
      { key: 'social' as const, ratio: 0.91 },
      { key: 'on' as const, ratio: 0.13 },
      { key: 'off' as const, ratio: 0.57 }
    ]

    expect(composeChannelReveal(source, width, height, categories, 987654321)).toEqual(
      composeWithRects(source, width, height, categories, 987654321)
    )
  })

  it('uses one shared path so equal progress stacks all channels on the same pixels', () => {
    const width = 17
    const height = 11
    const pixels = composeChannelReveal(
      sourcePixels(width, height),
      width,
      height,
      [
        { key: 'on', ratio: 0.5 },
        { key: 'off', ratio: 0.5 },
        { key: 'social', ratio: 0.5 }
      ],
      123
    )
    let fullColor = 0
    let white = 0

    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i] === 10) {
        fullColor++
        expect(pixels[i + 1]).toBe(20)
        expect(pixels[i + 2]).toBe(30)
      } else {
        white++
        expect(pixels[i]).toBe(255)
        expect(pixels[i + 1]).toBe(255)
        expect(pixels[i + 2]).toBe(255)
      }
    }
    expect(fullColor).toBe(Math.floor(0.5 * width * height))
    expect(fullColor + white).toBe(width * height)
  })

  it('restores every source channel at 100% and clamps ratios above one', () => {
    const width = 9
    const height = 8
    const source = sourcePixels(width, height)
    const pixels = composeChannelReveal(
      source,
      width,
      height,
      [
        { key: 'on', ratio: 2 },
        { key: 'off', ratio: 1 },
        { key: 'social', ratio: 1 }
      ],
      1
    )

    for (let i = 0; i < pixels.length; i += 4) {
      expect(pixels[i]).toBe(10)
      expect(pixels[i + 1]).toBe(20)
      expect(pixels[i + 2]).toBe(30)
      expect(pixels[i + 3]).toBe(255)
    }
  })

  it('keeps white when progress is zero or invalid, and rejects invalid inputs', () => {
    const width = 6
    const height = 4
    const source = sourcePixels(width, height)
    const zero = composeChannelReveal(
      source,
      width,
      height,
      [
        { key: 'on', ratio: 0 },
        { key: 'off', ratio: -1 },
        { key: 'social', ratio: Number.NaN }
      ],
      3
    )

    expect([...new Set(zero)]).toEqual([255])
    expect(composeChannelReveal(source, 0, height, [], 1)).toEqual(new Uint8ClampedArray())
    expect(composeChannelReveal(source, width, height, [], Number.NaN)).toEqual(
      new Uint8ClampedArray()
    )
    expect(composeChannelReveal(new Uint8ClampedArray(4), width, height, [], 1)).toEqual(
      new Uint8ClampedArray()
    )
  })
})
