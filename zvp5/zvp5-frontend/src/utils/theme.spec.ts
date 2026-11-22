import { describe, it, expect } from 'vitest'
import {
  THEME_IDS,
  THEME_PATHS,
  currentThemeId,
  isRootApp,
  isThemePath,
  normalizeTheme,
  readStoredTheme,
  setStoredTheme,
  themePath
} from './theme'

/**
 * 主题 = 整站多构建：这里只测「id ↔ 路径」映射与设备级缓存，
 * 不再有 data-theme-variant / 外壳注册表。
 */

function memoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v)
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size
    }
  } as Storage
}

describe('utils/theme', () => {
  describe('themePath', () => {
    it('canary 挂在根路径', () => {
      expect(themePath('canary')).toBe('/')
    })

    it('canary-pro 挂在 /t/canary-pro/', () => {
      expect(themePath('canary-pro')).toBe('/t/canary-pro/')
    })

    it('每个主题的路径都是站内绝对路径且互不相同', () => {
      const paths = THEME_IDS.map((id) => themePath(id))
      expect(paths.every((p) => p.startsWith('/'))).toBe(true)
      expect(new Set(paths).size).toBe(paths.length)
    })

    it('未知 id 回落到 canary 根路径', () => {
      expect(themePath('unknown')).toBe('/')
      expect(themePath('')).toBe('/')
    })
  })

  describe('currentThemeId', () => {
    it('根路径与普通页面属于 canary', () => {
      expect(currentThemeId('/')).toBe('canary')
      expect(currentThemeId('/user')).toBe('canary')
      expect(currentThemeId('/activities/1')).toBe('canary')
    })

    it('子应用路径反查回自身 id', () => {
      expect(currentThemeId('/t/canary-pro/')).toBe('canary-pro')
      expect(currentThemeId('/t/canary-pro/me')).toBe('canary-pro')
      expect(currentThemeId('/t/canary2/volunteers')).toBe('canary2')
    })

    it('前缀相同但不属于子应用的路径仍是 canary', () => {
      expect(currentThemeId('/t/canary-pro-other')).toBe('canary')
      expect(currentThemeId('/t/unknown/')).toBe('canary')
    })
  })

  describe('isThemePath / isRootApp', () => {
    it('只在对应子应用路径下为真', () => {
      expect(isThemePath('/t/canary-pro/me', 'canary-pro')).toBe(true)
      expect(isThemePath('/t/canary-pro/me', 'canary2')).toBe(false)
      expect(isThemePath('/', 'canary')).toBe(false)
    })

    it('根应用判定只认 canary', () => {
      expect(isRootApp('/')).toBe(true)
      expect(isRootApp('/t/canary2/')).toBe(false)
      expect(isRootApp('/t/canary-pro/')).toBe(false)
    })
  })

  describe('normalizeTheme', () => {
    it('合法 id 原样返回', () => {
      expect(normalizeTheme('canary')).toBe('canary')
      expect(normalizeTheme('canary-pro')).toBe('canary-pro')
    })

    it('非字符串 / 未知 id 回落到 canary', () => {
      expect(normalizeTheme(null)).toBe('canary')
      expect(normalizeTheme(undefined)).toBe('canary')
      expect(normalizeTheme(123)).toBe('canary')
      expect(normalizeTheme('custom-theme')).toBe('canary')
    })

    it('可用 allowed 收窄合法集合', () => {
      expect(normalizeTheme('canary2', ['canary', 'canary-pro'])).toBe('canary')
      expect(normalizeTheme('canary-pro', ['canary', 'canary-pro'])).toBe('canary-pro')
    })
  })

  describe('readStoredTheme / setStoredTheme', () => {
    it('写入后能读回', () => {
      const storage = memoryStorage()
      setStoredTheme('canary-pro', storage)
      expect(storage.getItem('zvms.theme')).toBe('canary-pro')
      expect(readStoredTheme(storage)).toBe('canary-pro')
    })

    it('未存储时读回 canary', () => {
      expect(readStoredTheme(memoryStorage())).toBe('canary')
    })

    it('存储里是未知值时读回 canary', () => {
      const storage = memoryStorage()
      storage.setItem('zvms.theme', 'nope')
      expect(readStoredTheme(storage)).toBe('canary')
    })

    it('setStoredTheme 写入规范化后的值并返回它', () => {
      const storage = memoryStorage()
      expect(setStoredTheme('unknown', storage)).toBe('canary')
      expect(storage.getItem('zvms.theme')).toBe('canary')
    })

    it('存储抛错时不影响返回值', () => {
      const broken = {
        getItem: () => {
          throw new Error('denied')
        },
        setItem: () => {
          throw new Error('denied')
        }
      }
      expect(readStoredTheme(broken)).toBe('canary')
      expect(setStoredTheme('canary-pro', broken)).toBe('canary-pro')
    })
  })

  it('THEME_PATHS 覆盖三种主题', () => {
    expect(THEME_IDS).toEqual(['canary', 'canary2', 'canary-pro'])
    expect(THEME_PATHS.canary2).toBe('/t/canary2/')
  })
})
