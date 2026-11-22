import { describe, expect, it } from 'vitest'
import { THEME_REGISTRY, getThemeShell, isValidTheme, THEME_IDS } from './registry'

/**
 * 主题注册表是「三套设计包」的唯一入口：
 * canary / canary-pro 必须解析到不同的外壳组件，不能退化成同一外壳换色。
 */
describe('themes/registry', () => {
  it('注册三种主题，且 id 稳定', () => {
    expect(THEME_IDS).toEqual(['canary', 'canary2', 'canary-pro'])
    expect(THEME_REGISTRY.map((theme) => theme.label)).toEqual([
      'Canary',
      'Canary 2',
      'Canary Pro'
    ])
  })

  it('canary 与 canary-pro 映射到不同外壳和不同设计类', () => {
    const canary = getThemeShell('canary')
    const pro = getThemeShell('canary-pro')

    expect(canary.shell).toBeDefined()
    expect(pro.shell).toBeDefined()
    expect(canary.shell).not.toBe(pro.shell)
    expect(canary.className).toBe('theme-canary')
    expect(pro.className).toBe('theme-canary-pro')
    expect(canary.needsBottomInset).toBe(true)
    expect(pro.needsBottomInset).toBe(false)
  })

  it('三种主题都声明了互不相同的外壳', () => {
    const shells = THEME_REGISTRY.map((theme) => theme.shell)
    expect(new Set(shells).size).toBe(THEME_REGISTRY.length)
  })

  it('未知主题回落到 canary，isValidTheme 只认注册表', () => {
    expect(getThemeShell('unknown')).toBe(getThemeShell('canary'))
    expect(isValidTheme('canary2')).toBe(true)
    expect(isValidTheme('unknown')).toBe(false)
  })

  it('登录页/维护页不套登录后外壳', () => {
    for (const theme of THEME_REGISTRY) {
      expect(theme.shellForAuthPages).toBe(false)
    }
  })
})
