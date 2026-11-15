import { describe, expect, it } from 'vitest'
import { normalizeOcrBox } from './ocrBox'

describe('normalizeOcrBox', () => {
  it('解析腾讯 {X,Y,Width,Height}', () => {
    expect(normalizeOcrBox({ X: 10, Y: 20, Width: 30, Height: 40 })).toEqual({
      left: 10,
      top: 20,
      width: 30,
      height: 40
    })
  })

  it('解析百度 {left,top,width,height}', () => {
    expect(normalizeOcrBox({ left: 12, top: 8, width: 100, height: 24 })).toEqual({
      left: 12,
      top: 8,
      width: 100,
      height: 24
    })
  })

  it('解析 {x,y,w,h}', () => {
    expect(normalizeOcrBox({ x: 1, y: 2, w: 3, h: 4 })).toEqual({
      left: 1,
      top: 2,
      width: 3,
      height: 4
    })
  })

  it('解析 {x,y,width,height}', () => {
    expect(normalizeOcrBox({ x: 5, y: 6, width: 7, height: 8 })).toEqual({
      left: 5,
      top: 6,
      width: 7,
      height: 8
    })
  })

  it('解析四点数组，取 min/max 作为外接矩形', () => {
    expect(
      normalizeOcrBox([
        [20, 30],
        [120, 30],
        [120, 60],
        [20, 60]
      ])
    ).toEqual({ left: 20, top: 30, width: 100, height: 30 })
  })

  it('解析两点数组 [x1,y1,x2,y2]，坐标乱序也能纠正', () => {
    expect(normalizeOcrBox([120, 60, 20, 30])).toEqual({
      left: 20,
      top: 30,
      width: 100,
      height: 30
    })
  })

  it('解析两点数组 [[x1,y1],[x2,y2]]', () => {
    expect(
      normalizeOcrBox([
        [20, 30],
        [120, 60]
      ])
    ).toEqual({ left: 20, top: 30, width: 100, height: 30 })
  })

  it('解析阿里的点集对象 [{x,y}×4]', () => {
    expect(
      normalizeOcrBox([
        { x: 4, y: 6 },
        { x: 44, y: 6 },
        { x: 44, y: 26 },
        { x: 4, y: 26 }
      ])
    ).toEqual({ left: 4, top: 6, width: 40, height: 20 })
  })

  it('解析 {pos:{x,y,width,height}} 包裹形状', () => {
    expect(normalizeOcrBox({ pos: { x: 9, y: 10, width: 11, height: 12 } })).toEqual({
      left: 9,
      top: 10,
      width: 11,
      height: 12
    })
  })

  it('解析 {pos:[{x,y}...]} 包裹形状', () => {
    expect(
      normalizeOcrBox({
        pos: [
          { x: 0, y: 0 },
          { x: 10, y: 10 }
        ]
      })
    ).toEqual({ left: 0, top: 0, width: 10, height: 10 })
  })

  it('尺寸非正或坐标缺失返回 null', () => {
    expect(normalizeOcrBox(null)).toBeNull()
    expect(normalizeOcrBox(undefined)).toBeNull()
    expect(normalizeOcrBox([])).toBeNull()
    expect(normalizeOcrBox([1, 2, 3])).toBeNull()
    expect(normalizeOcrBox([10, 20, 10, 40])).toBeNull()
    expect(normalizeOcrBox({ left: 10, top: 20 })).toBeNull()
    expect(normalizeOcrBox({ x: 1, y: 2, width: 0, height: 4 })).toBeNull()
    expect(normalizeOcrBox('not-a-box')).toBeNull()
    expect(normalizeOcrBox({ pos: { x: 1, y: 2 } })).toBeNull()
  })
})
