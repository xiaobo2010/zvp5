import { describe, expect, it } from 'vitest'
import { bytesToMb, formatMb, mbToBytes } from './userQuotaUnits'

describe('附件配额单位换算', () => {
  it('MB → 字节按 1024 进制', () => {
    expect(mbToBytes(100)).toBe(104857600)
    expect(mbToBytes(32)).toBe(33554432)
    expect(mbToBytes(0.5)).toBe(524288)
    expect(mbToBytes(0)).toBe(0)
  })

  it('空值透传 null（表示继承全局）', () => {
    expect(mbToBytes(null)).toBe(null)
    expect(mbToBytes(undefined)).toBe(null)
    expect(bytesToMb(null)).toBe(null)
    expect(bytesToMb(undefined)).toBe(null)
  })

  it('字节 → MB 保留一位小数', () => {
    expect(bytesToMb(104857600)).toBe(100)
    expect(bytesToMb(1572864)).toBe(1.5)
    expect(bytesToMb(0)).toBe(0)
  })

  it('展示文本去掉多余的 .0，空值给破折号', () => {
    expect(formatMb(104857600)).toBe('100')
    expect(formatMb(1572864)).toBe('1.5')
    expect(formatMb(null)).toBe('—')
  })
})
