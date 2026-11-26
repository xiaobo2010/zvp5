import { describe, expect, it } from 'vitest'
import { ART_PIECES, getArtPiece, getArtProgress, revealRgb, stableHash } from './artHome'

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

  it('reveals RGB channels as crisp pixels without partial blending', () => {
    const source = new Uint8ClampedArray([20, 40, 60, 255, 100, 120, 140, 255])
    const progress = [
      { key: 'on' as const, label: 'on', value: 1, target: 2, ratio: 0.5 },
      { key: 'off' as const, label: 'off', value: 1, target: 1, ratio: 1 },
      { key: 'social' as const, label: 'social', value: 0, target: 1, ratio: 0 }
    ]
    const first = revealRgb(source, 2, 1, progress, stableHash('account'))
    const second = revealRgb(source, 2, 1, progress, stableHash('account'))
    expect([...first]).toEqual([...second])
    expect([...first]).toEqual([20, 40, 250, 255, 250, 120, 250, 255])
  })
})
