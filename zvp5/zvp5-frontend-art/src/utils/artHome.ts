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

// Deterministic, hard-edged RGB reveal. Each pixel/channel is either painted or unpainted.
export function revealRgb(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  progress: ArtProgress[],
  seed: number
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(width * height * 4)
  output.fill(255)
  const ranks = new Uint32Array(width * height)
  for (let i = 0; i < ranks.length; i++) ranks[i] = i
  let state = seed >>> 0
  const random = () => {
    state = (state + 0x6d2b79f5) >>> 0
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return (value ^ (value >>> 14)) >>> 0
  }
  // Shuffle 4px blocks, then reveal every pixel inside a block in row-major order.
  const tile = 4
  const blocks: number[] = []
  for (let y = 0; y < height; y += tile)
    for (let x = 0; x < width; x += tile) blocks.push(y * width + x)
  for (let i = blocks.length - 1; i > 0; i--) {
    const j = random() % (i + 1)
    ;[blocks[i], blocks[j]] = [blocks[j]!, blocks[i]!]
  }
  let ordinal = 0
  for (const origin of blocks) {
    const x0 = origin % width
    const y0 = Math.floor(origin / width)
    for (let y = y0; y < Math.min(height, y0 + tile); y++)
      for (let x = x0; x < Math.min(width, x0 + tile); x++) ranks[y * width + x] = ordinal++
  }
  const total = width * height
  // Crisp paper tone for pixels that have not been revealed yet.
  const unpainted = 250
  for (let i = 0; i < total; i++) {
    const offset = i * 4
    const rank = ranks[i]! / total
    for (let channel = 0; channel < 3; channel++) {
      const item = progress[channel]!
      output[offset + channel] = rank < item.ratio ? source[offset + channel]! : unpainted
    }
    output[offset + 3] = 255
  }
  return output
}
