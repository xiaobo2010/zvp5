import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ZThemeStats from './ZThemeStats.vue'

/**
 * 管理端主题统计面板：
 * - 总数、每个主题的 count/percent、未识别 other 都要渲染；
 * - 403 显示无权限，而不是普通加载失败；
 * - 组件只依赖 ``api.theme.getThemeStats``，不碰真实请求。
 */

const { getThemeStats } = vi.hoisted(() => ({ getThemeStats: vi.fn() }))

vi.mock('@/api', () => ({
  default: { theme: { getThemeStats } }
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

const stubs = {
  ElSkeleton: { template: '<div class="skeleton-stub" />' },
  ElAlert: {
    props: ['title'],
    template: '<div class="alert-stub">{{ title }}</div>'
  }
}

function mountPanel() {
  return mount(ZThemeStats, { global: { stubs } })
}

describe('ZThemeStats', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders total, per-theme counts and the unrecognised bucket', async () => {
    getThemeStats.mockResolvedValue({
      total: 10,
      items: [
        { id: 'canary', label: 'Canary', count: 6, percent: 60 },
        { id: 'canary-pro', label: 'Canary Pro', count: 3, percent: 30 }
      ],
      other: 1
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('manage.themes.total')
    expect(wrapper.find('.theme-stats-total').text()).toBe('10')
    expect(wrapper.text()).toContain('Canary Pro')
    expect(wrapper.text()).toContain('manage.themes.other')

    // other=1 / total=10 -> 10%
    const bars = wrapper.findAll<HTMLElement>('.theme-stat-fill')
    expect(bars).toHaveLength(3)
    expect(bars[1].attributes('style')).toContain('width: 30%')
    expect(bars[2].attributes('style')).toContain('width: 10%')
  })

  it('shows the permission message on a 403', async () => {
    getThemeStats.mockRejectedValue({ status: 403, code: 'AI_FORBIDDEN' })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.alert-stub').text()).toBe('manage.themes.noPermission')
  })

  it('shows the load-failed message for other errors', async () => {
    getThemeStats.mockRejectedValue(new Error('boom'))

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.alert-stub').text()).toBe('manage.themes.loadFailed')
  })
})
