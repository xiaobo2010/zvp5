import { describe, expect, it } from 'vitest'
import {
  ART_PIECES,
  artChannelRatios,
  getArtPiece,
  getArtProgress,
  artPixelOrder,
  createArtFrame,
  stableHash
} from './artHome'

describe('Art home assets and reveal', () => {
  it('assigns the same image to the same account', () => {
    expect(getArtPiece('account-7')).toEqual(getArtPiece('account-7'))
  })

  it('keeps a metadata-free pool of thirteen image assets', () => {
    expect(ART_PIECES).toHaveLength(13)
    expect(new Set(ART_PIECES.map((piece) => piece.id)).size).toBe(13)
    expect(new Set(ART_PIECES.map((piece) => piece.src)).size).toBe(13)
    for (const piece of ART_PIECES) {
      expect(Object.keys(piece).sort()).toEqual(['id', 'src'])
    }
  })

  it('keeps the volunteer hour targets and complementary conversion', () => {
    const result = getArtProgress({ onCampus: 30, offCampus: 17, socialPractice: 9 })
    expect(result.map(({ value, target }) => [value, target])).toEqual([
      [31, 25],
      [19, 15],
      [9, 18]
    ])
    expect(
      getArtProgress({ onCampus: 0, offCampus: 0, socialPractice: 0 }).map((item) => item.ratio)
    ).toEqual([0, 0, 0])
    expect(
      getArtProgress({ onCampus: 100, offCampus: 100, socialPractice: 100 }).map(
        (item) => item.ratio
      )
    ).toEqual([1, 1, 1])
  })

  it('lifts every intermediate channel conservatively and preserves both endpoints', () => {
    for (const onCampus of [0, 2, 12, 25, 32]) {
      for (const offCampus of [0, 8, 15, 17]) {
        for (const socialPractice of [0, 8, 18]) {
          const progress = getArtProgress({ onCampus, offCampus, socialPractice })
          const lifted = artChannelRatios(progress)
          progress.forEach(({ ratio }, i) => {
            expect(lifted[i]).toBeGreaterThanOrEqual(ratio)
            expect(lifted[i]! - ratio).toBeLessThanOrEqual(2 / 15 + 1e-10)
            expect(lifted[i] === 1).toBe(ratio === 1)
          })
          if (!onCampus && !offCampus && !socialPractice) expect(lifted).toEqual([0, 0, 0])
        }
      }
    }
  })

  it('reveals RGB channels as crisp pixels without partial blending', () => {
    const source = new Uint8ClampedArray([20, 40, 60, 255, 100, 120, 140, 255])
    const progress = [
      { key: 'on' as const, label: 'on', value: 1, target: 2, ratio: 0.5 },
      { key: 'off' as const, label: 'off', value: 1, target: 1, ratio: 1 },
      { key: 'social' as const, label: 'social', value: 0, target: 1, ratio: 0 }
    ]
    const first = createArtFrame(
      source,
      artPixelOrder(2, 1, stableHash('account')),
      progress
    ).pixels
    const second = createArtFrame(
      source,
      artPixelOrder(2, 1, stableHash('account')),
      progress
    ).pixels
    expect([...first]).toEqual([...second])
    expect([...first]).toEqual([20, 40, 60, 255, 100, 120, 250, 255])
  })
  it('keeps the exact number of altered pixels even at the handover and across cycles', () => {
    const order = artPixelOrder(41, 47, 123)
    const source = new Uint8ClampedArray(order.length * 4).fill(250)
    const frame = createArtFrame(
      source,
      order,
      getArtProgress({ onCampus: 12, offCampus: 7, socialPractice: 8 })
    )
    const count = () =>
      order.reduce(
        (n, i) =>
          n +
          Number(
            frame.pixels[i * 4] !== 250 ||
              frame.pixels[i * 4 + 1] !== 250 ||
              frame.pixels[i * 4 + 2] !== 250
          ),
        0
      )
    const initial = count()
    const before = frame.pixels.slice()
    for (let i = 0; i < 3; i++) {
      const tick = frame.beginBreath()!
      for (const phase of [0, 0.25, 0.49, 0.5, 0.51, 0.75, 1]) {
        tick(phase)
        expect(count()).toBe(initial)
      }
    }
    expect(frame.pixels).not.toEqual(before)
  })
})
