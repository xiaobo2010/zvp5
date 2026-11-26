import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  RENDER_MODE_ATTR,
  RENDER_MODE_STORAGE_KEY,
  applyRenderMode,
  installRenderMode,
  normalizeRenderMode,
  readRenderMode,
  setRenderMode
} from './renderMode'

afterEach(() => {
  localStorage.removeItem(RENDER_MODE_STORAGE_KEY)
  document.documentElement.removeAttribute(RENDER_MODE_ATTR)
  vi.restoreAllMocks()
})

describe('renderMode 精细/流畅偏好', () => {
  it('未知值一律回落精细（原版）', () => {
    expect(normalizeRenderMode(undefined)).toBe('fine')
    expect(normalizeRenderMode(null)).toBe('fine')
    expect(normalizeRenderMode('')).toBe('fine')
    expect(normalizeRenderMode('high')).toBe('fine')
    expect(normalizeRenderMode('smooth')).toBe('smooth')
  })

  it('没有存过偏好时默认精细', () => {
    expect(readRenderMode()).toBe('fine')
  })

  it('读取已存的流畅偏好', () => {
    localStorage.setItem(RENDER_MODE_STORAGE_KEY, 'smooth')
    expect(readRenderMode()).toBe('smooth')
  })

  it('存储损坏时回落精细，不抛错', () => {
    const storage = {
      getItem: () => {
        throw new Error('SecurityError')
      }
    }
    expect(readRenderMode(storage)).toBe('fine')
  })

  it('applyRenderMode 只写属性，不落盘', () => {
    expect(applyRenderMode('smooth')).toBe('smooth')
    expect(document.documentElement.getAttribute(RENDER_MODE_ATTR)).toBe('smooth')
    expect(localStorage.getItem(RENDER_MODE_STORAGE_KEY)).toBeNull()
  })

  it('setRenderMode 立即生效并持久化', () => {
    expect(setRenderMode('smooth')).toBe('smooth')
    expect(document.documentElement.getAttribute(RENDER_MODE_ATTR)).toBe('smooth')
    expect(localStorage.getItem(RENDER_MODE_STORAGE_KEY)).toBe('smooth')

    expect(setRenderMode('fine')).toBe('fine')
    expect(document.documentElement.getAttribute(RENDER_MODE_ATTR)).toBe('fine')
    expect(localStorage.getItem(RENDER_MODE_STORAGE_KEY)).toBe('fine')
  })

  it('存储写入失败时本次切换仍然生效', () => {
    const storage = {
      setItem: () => {
        throw new Error('QuotaExceededError')
      }
    }
    expect(setRenderMode('smooth', { storage })).toBe('smooth')
    expect(document.documentElement.getAttribute(RENDER_MODE_ATTR)).toBe('smooth')
  })

  it('installRenderMode 在启动时应用已存偏好', () => {
    localStorage.setItem(RENDER_MODE_STORAGE_KEY, 'smooth')
    expect(installRenderMode()).toBe('smooth')
    expect(document.documentElement.getAttribute(RENDER_MODE_ATTR)).toBe('smooth')
  })
})
