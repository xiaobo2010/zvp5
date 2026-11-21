import { describe, it, expect, beforeEach } from 'vitest'
import { normalizeTheme, applyTheme, readStoredTheme, setStoredTheme, installTheme } from './theme'

describe('utils/theme', () => {
  let testDoc: Document
  let testStorage: Storage

  beforeEach(() => {
    // 创建测试文档
    testDoc = document.implementation.createHTMLDocument('test')
    // 创建测试存储
    const store = new Map<string, string>()
    testStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
      clear: () => store.clear(),
      key: () => null,
      length: 0
    }
  })

  describe('normalizeTheme', () => {
    it('should return input when valid string', () => {
      expect(normalizeTheme('canary')).toBe('canary')
      expect(normalizeTheme('canary-pro')).toBe('canary-pro')
    })

    it('should return canary for non-string input', () => {
      expect(normalizeTheme(null)).toBe('canary')
      expect(normalizeTheme(undefined)).toBe('canary')
      expect(normalizeTheme(123)).toBe('canary')
      expect(normalizeTheme({})).toBe('canary')
    })

    it('should return canary for unknown theme when allowed list provided', () => {
      expect(normalizeTheme('unknown', ['canary', 'canary-pro'])).toBe('canary')
    })

    it('should accept known theme from allowed list', () => {
      expect(normalizeTheme('canary-pro', ['canary', 'canary-pro'])).toBe('canary-pro')
    })

    it('should accept any string when no allowed list', () => {
      expect(normalizeTheme('custom-theme')).toBe('custom-theme')
    })
  })

  describe('applyTheme', () => {
    it('should set data-theme-variant attribute on document element', () => {
      applyTheme('canary', testDoc)
      expect(testDoc.documentElement.getAttribute('data-theme-variant')).toBe('canary')
    })

    it('should update existing attribute', () => {
      applyTheme('canary', testDoc)
      applyTheme('canary-pro', testDoc)
      expect(testDoc.documentElement.getAttribute('data-theme-variant')).toBe('canary-pro')
    })
  })

  describe('readStoredTheme', () => {
    it('should return stored theme from localStorage', () => {
      testStorage.setItem('zvms.theme', 'canary-pro')
      expect(readStoredTheme(testStorage)).toBe('canary-pro')
    })

    it('should return canary when nothing stored', () => {
      expect(readStoredTheme(testStorage)).toBe('canary')
    })
  })

  describe('setStoredTheme', () => {
    it('should write theme to localStorage', () => {
      setStoredTheme('canary-pro', testStorage)
      expect(testStorage.getItem('zvms.theme')).toBe('canary-pro')
    })

    it('should normalize and return theme', () => {
      const result = setStoredTheme('canary-pro', testStorage)
      expect(result).toBe('canary-pro')
    })
  })

  describe('installTheme', () => {
    it('should apply stored theme to document', () => {
      testStorage.setItem('zvms.theme', 'canary-pro')
      installTheme(testDoc, testStorage)
      expect(testDoc.documentElement.getAttribute('data-theme-variant')).toBe('canary-pro')
    })

    it('should apply default theme when nothing stored', () => {
      installTheme(testDoc, testStorage)
      expect(testDoc.documentElement.getAttribute('data-theme-variant')).toBe('canary')
    })
  })
})
