import { describe, it, expect } from 'vitest'
import {
  DEFAULT_THEME,
  THEME_PREFIX,
  currentThemeId,
  isRootApp,
  isThemeId,
  isThemePath,
  normalizeTheme,
  readStoredTheme,
  setStoredTheme,
  themePath
} from './theme'

/**
 * 主题 = 整站多构建：这里只测统一路径约定（`/` + `/themes/<id>/`）与设备级缓存。
 * 主题集合不在这里硬编码——它来自 `zvp5/themes/manifest.json` / 后端注册表。
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
  it('路径约定是唯一的：默认主题在根，其它主题在 /themes/<id>/', () => {
    expect(DEFAULT_THEME).toBe('canary')
    expect(THEME_PREFIX).toBe('/themes/')
  })

  describe('themePath', () => {
    it('canary 挂在根路径', () => {
      expect(themePath('canary')).toBe('/')
    })

    it('canary-pro 挂在 /themes/canary-pro/', () => {
      expect(themePath('canary-pro')).toBe('/themes/canary-pro/')
    })

    it('任意合法 slug 都按统一前缀派生路径', () => {
      expect(themePath('canary2')).toBe('/themes/canary2/')
      expect(themePath('brand-new-theme')).toBe('/themes/brand-new-theme/')
    })

    it('未知/非法 id 回落到 canary 根路径', () => {
      expect(themePath('')).toBe('/')
      expect(themePath('Canary')).toBe('/')
      expect(themePath('canary pro')).toBe('/')
      expect(themePath('../etc/passwd')).toBe('/')
      expect(themePath('/themes/canary2/')).toBe('/')
      expect(themePath(null as unknown as string)).toBe('/')
    })

    it('传 allowed 时收窄到已部署的主题集合', () => {
      expect(themePath('canary2', ['canary', 'canary-pro'])).toBe('/')
      expect(themePath('canary-pro', ['canary', 'canary-pro'])).toBe('/themes/canary-pro/')
    })
  })

  describe('currentThemeId', () => {
    it('根路径与普通页面属于 canary', () => {
      expect(currentThemeId('/')).toBe('canary')
      expect(currentThemeId('/user')).toBe('canary')
      expect(currentThemeId('/activities/1')).toBe('canary')
      expect(currentThemeId('')).toBe('canary')
    })

    it('子应用路径反查回自身 id', () => {
      expect(currentThemeId('/themes/canary-pro/')).toBe('canary-pro')
      expect(currentThemeId('/themes/canary-pro/me')).toBe('canary-pro')
      expect(currentThemeId('/themes/canary2/volunteers')).toBe('canary2')
      expect(currentThemeId('/themes/canary2')).toBe('canary2')
    })

    it('非法前缀 / 非 slug 段不属于任何子应用', () => {
      expect(currentThemeId('/themes/')).toBe('canary')
      expect(currentThemeId('/themes/UNKNOWN/')).toBe('canary')
      expect(currentThemeId('/theme/canary2/')).toBe('canary')
      expect(currentThemeId('/assets/canary2/')).toBe('canary')
    })

    it('路径段按完整 slug 匹配，不会被更长的 id 误判', () => {
      expect(currentThemeId('/themes/canary2-other')).toBe('canary2-other')
      expect(currentThemeId('/themes/canary2x')).toBe('canary2x')
    })
  })

  describe('isThemeId / isThemePath / isRootApp', () => {
    it('isThemeId 只判断 slug 语法', () => {
      expect(isThemeId('canary2')).toBe(true)
      expect(isThemeId('canary-pro')).toBe(true)
      expect(isThemeId('Canary2')).toBe(false)
      expect(isThemeId('-canary')).toBe(false)
      expect(isThemeId('')).toBe(false)
      expect(isThemeId(undefined)).toBe(false)
    })

    it('isThemePath 只在对应子应用路径下为真', () => {
      expect(isThemePath('/themes/canary-pro/me', 'canary-pro')).toBe(true)
      expect(isThemePath('/themes/canary-pro/me', 'canary2')).toBe(false)
      expect(isThemePath('/', 'canary')).toBe(false)
    })

    it('根应用判定只认 canary', () => {
      expect(isRootApp('/')).toBe(true)
      expect(isRootApp('/themes/canary2/')).toBe(false)
      expect(isRootApp('/themes/canary-pro/')).toBe(false)
    })
  })

  describe('normalizeTheme', () => {
    it('合法 id 原样返回', () => {
      expect(normalizeTheme('canary')).toBe('canary')
      expect(normalizeTheme('canary-pro')).toBe('canary-pro')
      expect(normalizeTheme('future-theme')).toBe('future-theme')
    })

    it('非字符串 / 非法 slug 回落到 canary', () => {
      expect(normalizeTheme(null)).toBe('canary')
      expect(normalizeTheme(undefined)).toBe('canary')
      expect(normalizeTheme(123)).toBe('canary')
      expect(normalizeTheme('custom theme')).toBe('canary')
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

    it('存储里是非法值时读回 canary', () => {
      const storage = memoryStorage()
      storage.setItem('zvms.theme', 'not a theme')
      expect(readStoredTheme(storage)).toBe('canary')
    })

    it('setStoredTheme 写入规范化后的值并返回它', () => {
      const storage = memoryStorage()
      expect(setStoredTheme('Not A Theme', storage)).toBe('canary')
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
})
