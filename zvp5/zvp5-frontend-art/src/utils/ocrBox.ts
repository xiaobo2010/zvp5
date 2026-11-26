/**
 * OCR 行坐标归一化：把各引擎五花八门的 box 统一成「相对原图的像素矩形」。
 *
 * 各引擎出口形状（后端原样透传，不做归一）：
 * - 腾讯 `{X,Y,Width,Height}`
 * - 百度 `{left,top,width,height}`
 * - 阿里 `pos` 点集（`[{x,y},{x,y},...]`，通常是四个角）
 * - RapidOCR 四点数组 `[[x,y]×4]`
 * - Groq `[x1,y1,x2,y2]`
 * - 智谱 `null`（提示词里明确不要坐标）
 *
 * 页面只负责按原图尺寸换算成百分比，所以这里统一输出像素矩形；
 * 尺寸非正、坐标缺失的框一律返回 null —— 画不出来的框不画，比画歪更强。
 */
export interface OcrBoxRect {
  left: number
  top: number
  width: number
  height: number
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function fromEdges(x1: number, y1: number, x2: number, y2: number): OcrBoxRect | null {
  const left = Math.min(x1, x2)
  const top = Math.min(y1, y2)
  const width = Math.abs(x2 - x1)
  const height = Math.abs(y2 - y1)
  if (width <= 0 || height <= 0) return null
  return { left, top, width, height }
}

function fromPoints(points: Array<[number, number]>): OcrBoxRect | null {
  if (points.length < 2) return null
  let minX = points[0][0]
  let minY = points[0][1]
  let maxX = minX
  let maxY = minY
  for (const [x, y] of points.slice(1)) {
    if (x < minX) minX = x
    if (y < minY) minY = y
    if (x > maxX) maxX = x
    if (y > maxY) maxY = y
  }
  return fromEdges(minX, minY, maxX, maxY)
}

function readRect(record: Record<string, unknown>): OcrBoxRect | null {
  const left = toNumber(record.left ?? record.x ?? record.X)
  const top = toNumber(record.top ?? record.y ?? record.Y)
  if (left === null || top === null) return null

  const width = toNumber(record.width ?? record.w ?? record.Width)
  const height = toNumber(record.height ?? record.h ?? record.Height)
  if (width !== null && height !== null) {
    return fromEdges(left, top, left + width, top + height)
  }

  const right = toNumber(record.right ?? record.x2 ?? record.X2)
  const bottom = toNumber(record.bottom ?? record.y2 ?? record.Y2)
  if (right === null || bottom === null) return null
  return fromEdges(left, top, right, bottom)
}

function readArray(box: unknown[]): OcrBoxRect | null {
  if (!box.length) return null

  // 四点数组（RapidOCR）与点集对象（阿里 pos）都按「点的集合」处理。
  if (box.every((item) => Array.isArray(item) || (item !== null && typeof item === 'object'))) {
    const points: Array<[number, number]> = []
    for (const item of box) {
      if (Array.isArray(item)) {
        if (item.length < 2) return null
        const x = toNumber(item[0])
        const y = toNumber(item[1])
        if (x === null || y === null) return null
        points.push([x, y])
        continue
      }
      const point = item as Record<string, unknown>
      const x = toNumber(point.x ?? point.X)
      const y = toNumber(point.y ?? point.Y)
      if (x === null || y === null) return null
      points.push([x, y])
    }
    return fromPoints(points)
  }

  if (box.length === 4) {
    const [x1, y1, x2, y2] = box.map(toNumber)
    if (x1 === null || y1 === null || x2 === null || y2 === null) return null
    return fromEdges(x1, y1, x2, y2)
  }

  return null
}

export function normalizeOcrBox(box: unknown): OcrBoxRect | null {
  if (!box) return null
  if (Array.isArray(box)) return readArray(box)
  if (typeof box !== 'object') return null

  const record = box as Record<string, unknown>
  const rect = readRect(record)
  if (rect) return rect

  const pos = record.pos
  if (Array.isArray(pos)) return readArray(pos)
  if (pos && typeof pos === 'object') return readRect(pos as Record<string, unknown>)
  return null
}
