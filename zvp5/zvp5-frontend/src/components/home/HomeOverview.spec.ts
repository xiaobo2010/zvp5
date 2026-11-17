import { defineComponent, h, type PropType } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import HomeOverview from './HomeOverview.vue'
import { assignHomeArtwork, type HomeCategory, type HomeProgress } from '@/utils/homeArtwork'

const ArtworkStub = defineComponent({
  name: 'ArtworkStub',
  props: {
    assignment: { type: Object, default: null },
    categories: { type: Array as PropType<HomeCategory[]>, required: true },
    loading: Boolean
  },
  setup(props) {
    return () =>
      h('div', {
        class: 'art-stub',
        'data-categories': props.categories.map((category) => category.key).join(',')
      })
  }
})

function category(key: HomeCategory['key'], percent: number, ratio: number): HomeCategory {
  const labels: Record<HomeCategory['key'], string> = {
    on: '校内义工',
    off: '校外义工',
    social: '社会实践'
  }
  return {
    key,
    label: labels[key],
    raw: ratio * 25,
    effective: ratio * 25 + 2,
    target: 25,
    bonus: key === 'on' ? 2 : 0,
    percent,
    ratio,
    gap: ratio >= 1 ? 2 : -8
  }
}

const progress: HomeProgress = {
  categories: [category('social', 0, 0), category('on', 80, 0.8), category('off', 120, 1)],
  total: 20,
  hasBonus: true
}

function mountOverview(extra: Record<string, unknown> = {}) {
  return mount(HomeOverview, {
    props: {
      name: '林同学',
      number: '2026001',
      userKey: 'user-home-example',
      progress,
      loading: false,
      error: '',
      ...extra
    },
    global: { stubs: { HomeProgressArtwork: ArtworkStub } },
    slots: {
      identity: '<span class="identity-slot">身份标识</span>',
      footer: '<div class="footer-slot">页脚内容</div>'
    }
  })
}

describe('HomeOverview', () => {
  it('固定按红绿蓝排列进度，并把同一顺序传给作品', () => {
    const wrapper = mountOverview()
    const columns = wrapper.findAll('.yg-home-overview__category')

    expect(columns.map((column) => column.attributes('data-category'))).toEqual([
      'on',
      'off',
      'social'
    ])
    expect(columns[0]?.text()).toContain('红 · 校内义工')
    expect(columns[1]?.text()).toContain('绿 · 校外义工')
    expect(columns[2]?.text()).toContain('蓝 · 社会实践')
    expect(wrapper.get('.art-stub').attributes('data-categories')).toBe('on,off,social')
    wrapper.unmount()
  })

  it('缺失分类显示未知而不是伪造 0%，并始终保留三栏', async () => {
    const wrapper = mountOverview({
      progress: {
        categories: [progress.categories[0]!, progress.categories[1]!],
        total: 5,
        hasBonus: false
      }
    })
    const columns = wrapper.findAll('.yg-home-overview__category')

    expect(columns).toHaveLength(3)
    expect(columns[1]?.text()).toContain('暂未取得')
    expect(columns[1]?.text()).toContain('该项时长未知')
    expect(columns[1]?.find('[role="progressbar"]').exists()).toBe(false)
    expect(columns[1]?.text()).not.toContain('0%')
    expect(wrapper.findAll('[role="progressbar"]')).toHaveLength(2)
    expect(wrapper.get('.art-stub').attributes('data-categories')).toBe('on,social')

    await wrapper.setProps({ progress: { categories: [], total: 0, hasBonus: false } })
    expect(wrapper.findAll('.yg-home-overview__category')).toHaveLength(3)
    expect(wrapper.findAll('[role="progressbar"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('进度条提供可访问的数值与完成时长', () => {
    const wrapper = mountOverview()
    const bars = wrapper.findAll('[role="progressbar"]')

    expect(bars[0]?.attributes('aria-label')).toBe('校内义工')
    expect(bars[0]?.attributes('aria-valuenow')).toBe('80')
    expect(bars[0]?.attributes('aria-valuemin')).toBe('0')
    expect(bars[0]?.attributes('aria-valuemax')).toBe('100')
    expect(bars[0]?.attributes('aria-valuetext')).toContain('/ 25 小时')
    expect(bars[1]?.attributes('aria-valuenow')).toBe('100')
    wrapper.unmount()
  })

  it('刷新失败时保留已知进度和作品', () => {
    const wrapper = mountOverview({
      error: '刷新失败，正在显示上次成功取得的时长。',
      loading: false
    })

    expect(wrapper.find('.art-stub').exists()).toBe(true)
    expect(wrapper.findAll('.yg-home-overview__category')).toHaveLength(3)
    expect(wrapper.get('.yg-home-overview__error').text()).toContain('刷新失败')
    wrapper.unmount()
  })

  it('保留身份、页脚插槽与四个既有事件契约', async () => {
    const wrapper = mountOverview()
    expect(wrapper.get('.identity-slot').text()).toBe('身份标识')
    expect(wrapper.get('.footer-slot').text()).toBe('页脚内容')

    await wrapper.get('.yg-home-overview__profile').trigger('click')
    await wrapper.get('.yg-home-overview__tools .yg-btn--ghost').trigger('click')
    await wrapper.get('.yg-home-overview__tools .yg-btn--secondary').trigger('click')
    await wrapper.get('.yg-home-overview__tools .yg-btn--primary').trigger('click')

    expect(wrapper.emitted('profile')).toHaveLength(1)
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    expect(wrapper.emitted('details')).toHaveLength(1)
    expect(wrapper.emitted('activities')).toHaveLength(1)
    wrapper.unmount()
  })

  it('无未知数据时不显示虚假的三栏和 0 小时', () => {
    const wrapper = mountOverview({ progress: null })
    expect(wrapper.find('.yg-home-overview__progress').exists()).toBe(false)
    expect(wrapper.text()).toContain('取得时长后')
    expect(wrapper.get('.yg-home-overview__total').text()).toContain('—')
    wrapper.unmount()
  })

  it('以作品原始宽高驱动共享列宽变量', () => {
    const wrapper = mountOverview()
    const assigned = assignHomeArtwork('user-home-example')
    const ratio = Number(
      wrapper
        .get('.yg-home-overview__art-stack')
        .element.getAttribute('style')
        ?.match(/--yg-home-artwork-ratio:\s*([^;]+)/)?.[1]
    )

    expect(ratio).toBeCloseTo(assigned!.artwork.width / assigned!.artwork.height, 5)
    wrapper.unmount()
  })
})
