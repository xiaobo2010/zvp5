import art01 from '@/assets/art-home/art-01.jpg'
import art02 from '@/assets/art-home/art-02.jpg'
import art03 from '@/assets/art-home/art-03.jpg'
import art04 from '@/assets/art-home/art-04.jpg'
import art05 from '@/assets/art-home/art-05.jpg'
import art06 from '@/assets/art-home/art-06.jpg'
import art07 from '@/assets/art-home/art-07.jpg'
import art08 from '@/assets/art-home/art-08.jpg'
import art09 from '@/assets/art-home/art-09.jpg'
import art10 from '@/assets/art-home/art-10.jpg'
import art11 from '@/assets/art-home/art-11.jpg'
import art12 from '@/assets/art-home/art-12.jpg'
import art13 from '@/assets/art-home/art-13.jpg'

export interface ArtPiece {
  id: string
  src: string
}

export const ART_PIECES: ArtPiece[] = [
  { id: 'art-01', src: art01 },
  { id: 'art-02', src: art02 },
  { id: 'art-03', src: art03 },
  { id: 'art-04', src: art04 },
  { id: 'art-05', src: art05 },
  { id: 'art-06', src: art06 },
  { id: 'art-07', src: art07 },
  { id: 'art-08', src: art08 },
  { id: 'art-09', src: art09 },
  { id: 'art-10', src: art10 },
  { id: 'art-11', src: art11 },
  { id: 'art-12', src: art12 },
  { id: 'art-13', src: art13 }
]

export function stableHash(text: string): number {
  let hash = 2166136261
  for (let index = 0; index < text.length; index++)
    hash = Math.imul(hash ^ text.charCodeAt(index), 16777619)
  return hash >>> 0
}

export function getArtPiece(account: string): ArtPiece {
  return ART_PIECES[stableHash(account) % ART_PIECES.length]!
}

export interface ArtProgress {
  key: 'on' | 'off' | 'social'
  label: string
  value: number
  target: number
  ratio: number
}

export function getArtProgress(time: {
  onCampus: number
  offCampus: number
  socialPractice: number
}): ArtProgress[] {
  const on = Math.max(0, Number(time.onCampus) || 0)
  const off = Math.max(0, Number(time.offCampus) || 0)
  const social = Math.max(0, Number(time.socialPractice) || 0)
  const effectiveOn = on + (off > 15 ? Math.round((off - 15) / 2) : 0)
  const effectiveOff = off + (on > 25 ? Math.round((on - 25) / 3) : 0)
  return [
    {
      key: 'on',
      label: '校内义工',
      value: effectiveOn,
      target: 25,
      ratio: Math.min(1, effectiveOn / 25)
    },
    {
      key: 'off',
      label: '校外义工',
      value: effectiveOff,
      target: 15,
      ratio: Math.min(1, effectiveOff / 15)
    },
    { key: 'social', label: '社会实践', value: social, target: 18, ratio: Math.min(1, social / 18) }
  ]
}

// Only the painting gets a lift. The progress panel keeps the actual hour ratios.
export function artChannelRatios(progress: ArtProgress[]): number[] {
  const mean = progress.reduce((sum, item) => sum + item.ratio, 0) / 3
  return progress.map(({ ratio }) => ratio + 0.2 * mean * (1 - ratio))
}

// Store positions in reveal order. Consecutive ordinals form small paper tiles.
export function artPixelOrder(width: number, height: number, seed: number): Uint32Array {
  const tile = 4
  const cols = Math.ceil(width / tile)
  const blocks = Uint32Array.from({ length: cols * Math.ceil(height / tile) }, (_, i) => i)
  let state = seed >>> 0
  for (let i = blocks.length - 1; i > 0; i--) {
    state = (state + 0x6d2b79f5) >>> 0
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    const j = ((value ^ (value >>> 14)) >>> 0) % (i + 1)
    ;[blocks[i], blocks[j]] = [blocks[j]!, blocks[i]!]
  }
  const order = new Uint32Array(width * height)
  let next = 0
  for (const block of blocks) {
    const x0 = (block % cols) * tile
    const y0 = Math.floor(block / cols) * tile
    for (let y = y0; y < Math.min(y0 + tile, height); y++)
      for (let x = x0; x < Math.min(x0 + tile, width); x++) order[next++] = y * width + x
  }
  return order
}

export function createArtFrame(
  source: Uint8ClampedArray,
  order: Uint32Array,
  progress: ArtProgress[]
) {
  const pixels = new Uint8ClampedArray(source)
  const cutoffs = artChannelRatios(progress).map((ratio) => Math.ceil(ratio * order.length))
  const normal = Math.min(...cutoffs)
  function paint(pixel: number, ordinal: number, strength: number) {
    const start = pixel * 4
    for (let c = 0; c < 3; c++) {
      const original = source[start + c]!
      // Keep even near-paper source pixels distinct while this mask owns them.
      const delta = original === 250 ? -1 : 250 - original
      pixels[start + c] =
        ordinal < cutoffs[c]! || strength === 0
          ? original
          : original + Math.sign(delta) * Math.max(1, Math.round(Math.abs(delta) * strength))
    }
  }
  for (let i = normal; i < order.length; i++) paint(order[i]!, i, 1)

  function beginBreath(random = Math.random) {
    const count = Math.min(normal, order.length - normal, Math.ceil(order.length * 0.012))
    if (!count) return null
    // Sample short runs so neighbouring pixels move together. The sets exclude overlaps.
    function sample(start: number, length: number) {
      const positions = new Set<number>()
      // In a very small pool use a circular walk instead of repeated random retries.
      while (positions.size < count) {
        const first = Math.floor(random() * length)
        for (let j = 0; j < length && positions.size < count; j++) {
          const pos = start + ((first + j) % length)
          if (!positions.has(pos)) {
            positions.add(pos)
            if (j >= 15) break
          }
        }
      }
      return [...positions]
    }
    const clear = sample(0, normal)
    const masked = sample(normal, order.length - normal)
    return (phase: number) => {
      // Fade towards the source, hand the mask over at its faintest, then fade back.
      // Exactly one pixel in each pair is abnormal on every displayed frame.
      const handover = phase >= 0.5
      const distance = Math.abs(2 * phase - 1)
      const strength = Math.max(Number.EPSILON, distance * distance * (3 - 2 * distance))
      for (let i = 0; i < count; i++) {
        const a = clear[i]!,
          b = masked[i]!
        paint(order[a]!, b, handover ? strength : 0)
        paint(order[b]!, b, handover ? 0 : strength)
        if (phase === 1) [order[a], order[b]] = [order[b]!, order[a]!]
      }
    }
  }
  return { pixels, beginBreath }
}
