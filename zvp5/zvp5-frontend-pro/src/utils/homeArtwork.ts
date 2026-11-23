// Account-stable artwork assignment and exact RGB pixel reveal.
import { HOME_ARTWORKS, type HomeArtwork } from '@/data/homeArtworks'

export interface HomeTimeInput {
  onCampus: number
  offCampus: number
  socialPractice: number
}

export type HomeCategoryKey = 'on' | 'off' | 'social'

export interface HomeCategory {
  key: HomeCategoryKey
  label: string
  raw: number
  effective: number
  target: number
  bonus: number
  percent: number
  ratio: number
  gap: number
}

export interface HomeProgress {
  categories: HomeCategory[]
  total: number
  hasBonus: boolean
}

export interface HomeArtworkAssignment {
  artwork: HomeArtwork
  seed: number
}

export interface RevealRect {
  x: number
  y: number
  width: number
  height: number
}

export const HOME_CATEGORY_CHANNEL: Record<HomeCategoryKey, 0 | 1 | 2> = {
  on: 0,
  off: 1,
  social: 2
}

export const HOME_CATEGORY_CHANNEL_LABELS: Record<HomeCategoryKey, string> = {
  on: '红',
  off: '绿',
  social: '蓝'
}

const validHours = (value: number) => (Number.isFinite(value) ? Math.max(0, value) : 0)
const round = (value: number) => Math.round(value * 100) / 100

export function calculateHomeProgress(input: HomeTimeInput): HomeProgress {
  const on = validHours(input.onCampus)
  const off = validHours(input.offCampus)
  const social = validHours(input.socialPractice)
  const onBonus = off > 15 ? Math.round((off - 15) / 2) : 0
  const offBonus = on > 25 ? Math.round((on - 25) / 3) : 0
  const category = (
    key: HomeCategoryKey,
    label: string,
    raw: number,
    target: number,
    bonus = 0
  ): HomeCategory => {
    const effective = round(raw + bonus)
    return {
      key,
      label,
      raw,
      target,
      bonus,
      effective,
      percent: round((effective / target) * 100),
      ratio: Math.min(1, effective / target),
      gap: round(effective - target)
    }
  }

  return {
    categories: [
      category('on', '校内义工', on, 25, onBonus),
      category('off', '校外义工', off, 15, offBonus),
      category('social', '社会实践', social, 18)
    ],
    total: round(on + off + social),
    hasBonus: onBonus > 0 || offBonus > 0
  }
}

// Storage-free deterministic assignment: the same account receives the same
// artwork and reveal path on every device and browser session.
function hash(text: string): number {
  let value = 2166136261
  for (let i = 0; i < text.length; i++) {
    value = Math.imul(value ^ text.charCodeAt(i), 16777619)
  }
  return value >>> 0
}

export function assignHomeArtwork(userKey: string): HomeArtworkAssignment | null {
  const key = userKey.trim()
  if (!key || HOME_ARTWORKS.length === 0) return null
  const artwork = HOME_ARTWORKS[hash(`home-artwork-v1:${key}`) % HOME_ARTWORKS.length]
  if (!artwork) return null
  return {
    artwork,
    seed: hash(`home-path-v1:${key}:${artwork.id}`)
  }
}

let revealOrderCache: { key: string; tiles: RevealRect[] } | null = null

function getRevealOrder(width: number, height: number, seed: number, tileSize: number): RevealRect[] {
  const key = `${width}x${height}:${seed}:${tileSize}`
  if (revealOrderCache?.key === key) return revealOrderCache.tiles
  const tiles = buildRevealOrder(width, height, seed, tileSize)
  revealOrderCache = tiles.length ? { key, tiles } : null
  return tiles
}

function buildRevealOrder(width: number, height: number, seed: number, tileSize: number): RevealRect[] {
  width = Math.floor(width)
  height = Math.floor(height)
  if (width < 1 || height < 1 || width > 4096 || height > 4096 || tileSize < 1) return []
  tileSize = Math.floor(tileSize)

  const tiles: RevealRect[] = []
  for (let y = 0; y < height; y += tileSize) {
    for (let x = 0; x < width; x += tileSize) {
      tiles.push({
        x,
        y,
        width: Math.min(tileSize, width - x),
        height: Math.min(tileSize, height - y)
      })
    }
  }

  // Mulberry32 + Fisher–Yates. The sequence depends only on dimensions and seed.
  let state = seed >>> 0
  const random = () => {
    let t = (state += 0x6d2b79f5)
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  for (let i = tiles.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1))
    const next = tiles[j]!
    tiles[j] = tiles[i]!
    tiles[i] = next
  }
  return tiles
}

export function buildRevealRects(
  width: number,
  height: number,
  ratio: number,
  seed: number,
  tileSize = 16
): RevealRect[] {
  if (![width, height, ratio, seed, tileSize].every(Number.isFinite)) return []
  width = Math.floor(width)
  height = Math.floor(height)
  if (width < 1 || height < 1 || width > 4096 || height > 4096 || tileSize < 1) return []

  let remaining = Math.floor(Math.max(0, Math.min(1, ratio)) * width * height)
  if (remaining <= 0) return []

  const tiles = buildRevealOrder(width, height, seed, Math.floor(tileSize))
  const visible: RevealRect[] = []
  for (const tile of tiles) {
    const area = tile.width * tile.height
    if (remaining >= area) {
      visible.push(tile)
      remaining -= area
    } else {
      const rows = Math.floor(remaining / tile.width)
      const columns = remaining % tile.width
      if (rows > 0) visible.push({ ...tile, height: rows })
      if (columns > 0) {
        visible.push({ x: tile.x, y: tile.y + rows, width: columns, height: 1 })
      }
      break
    }
    if (remaining === 0) break
  }
  return visible
}

function writeRevealChannel(
  source: Uint8ClampedArray | Uint8Array,
  pixels: Uint8ClampedArray,
  width: number,
  tile: RevealRect,
  channel: 0 | 1 | 2,
  count: number
): void {
  let remaining = count
  let row = 0
  while (remaining > 0) {
    const columns = Math.min(remaining, tile.width)
    let offset = ((tile.y + row) * width + tile.x) * 4
    for (let x = 0; x < columns; x++, offset += 4) {
      const alpha = source[offset + 3]! / 255
      const value = source[offset + channel]!
      pixels[offset + channel] =
        alpha === 1 ? value : Math.round(value * alpha + 255 * (1 - alpha))
    }
    remaining -= columns
    row++
  }
}

// All channels share one shuffled tile path. The pixel count is exact because
// the final tile is split into whole rows and a one-pixel-high remainder.
export function composeChannelReveal(
  source: Uint8ClampedArray | Uint8Array,
  width: number,
  height: number,
  categories: ReadonlyArray<Pick<HomeCategory, 'key' | 'ratio'>>,
  seed: number,
  output?: Uint8ClampedArray
): Uint8ClampedArray {
  width = Math.floor(width)
  height = Math.floor(height)
  const byteLength = width * height * 4
  if (
    ![width, height, seed].every(Number.isFinite) ||
    width < 1 ||
    height < 1 ||
    width > 4096 ||
    height > 4096 ||
    source.length < byteLength
  ) {
    return new Uint8ClampedArray()
  }

  const pixels = output?.length === byteLength ? output : new Uint8ClampedArray(byteLength)
  pixels.fill(255)

  const pixelsPerChannel = width * height
  const channels: Array<{ channel: 0 | 1 | 2; remaining: number }> = []
  for (const category of categories) {
    const channel = HOME_CATEGORY_CHANNEL[category.key]
    if (channel === undefined || !Number.isFinite(category.ratio) || category.ratio <= 0) continue
    const remaining = Math.floor(Math.min(1, category.ratio) * pixelsPerChannel)
    if (remaining > 0) channels.push({ channel, remaining })
  }
  if (channels.length === 0) return pixels

  let pendingChannels = channels.length
  for (const tile of getRevealOrder(width, height, seed, 16)) {
    const area = tile.width * tile.height
    for (const entry of channels) {
      if (entry.remaining === 0) continue
      const count = Math.min(entry.remaining, area)
      writeRevealChannel(source, pixels, width, tile, entry.channel, count)
      entry.remaining -= count
      if (entry.remaining === 0) pendingChannels--
    }
    if (pendingChannels === 0) break
  }

  return pixels
}


