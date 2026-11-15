import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { coreZhCN } from '@/i18n/locales/core'
import AboutView from './AboutView.vue'

/**
 * 关于页改造后只保留「版本 + 平台说明 + 折叠文档」三块，本组用例锁住：
 * 版本来自后端、平台与许可文案真的渲染出来、折叠项里有实际内容与真实链接，
 * 以及更新日志入口走 router 而不是裸 href。
 */

const { getVersionMock, pushMock, backMock } = vi.hoisted(() => ({
  getVersionMock: vi.fn(),
  pushMock: vi.fn(),
  backMock: vi.fn()
}))

vi.mock('@/api', () => ({
  default: { version: { getVersion: getVersionMock } }
}))

vi.mock('@/router', () => ({
  default: { push: pushMock, back: backMock }
}))

vi.mock('@/stores/header', () => ({
  useHeaderStore: () => ({ setHeader: vi.fn() })
}))

// element-plus 整包 mock：真实包会引入 theme-chalk 的 .css，node ESM 下无法加载。
// 折叠组件按「永远展开」的桩渲染，方便断言标题与内容同时出现在 DOM 里。
vi.mock('element-plus', async () => {
  const ElCollapse = defineComponent({
    name: 'ElCollapse',
    setup(_, { slots }) {
      return () => h('div', { class: 'el-collapse-stub' }, slots.default?.())
    }
  })

  const ElCollapseItem = defineComponent({
    name: 'ElCollapseItem',
    props: { title: String, name: String },
    setup(props, { slots }) {
      return () =>
        h('div', { class: 'el-collapse-item-stub' }, [
          h('div', { class: 'el-collapse-item__header' }, props.title),
          h('div', { class: 'el-collapse-item__content' }, slots.default?.())
        ])
    }
  })

  return { ElCollapse, ElCollapseItem }
})

function buildI18n() {
  return createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': coreZhCN } })
}

async function mountAbout() {
  const wrapper = mount(AboutView, { global: { plugins: [buildI18n()] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  getVersionMock.mockReset().mockResolvedValue('5.0.0-test')
  pushMock.mockReset()
  backMock.mockReset()
})

describe('AboutView', () => {
  it('版本、平台与许可说明都取实际值', async () => {
    const wrapper = await mountAbout()

    expect(getVersionMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.about-spec-version').text()).toBe('5.0.0-test')

    const text = wrapper.text()
    // 平台说明：Web（PWA）与支持设备，不能再是一句空壳标题。
    expect(text).toContain('Web（PWA）')
    expect(text).toContain('桌面浏览器 · 平板')
    expect(text).toContain('基于 MIT 证书开源')
    expect(text).toContain(coreZhCN.about.alternative)
    expect(text).toContain(coreZhCN.about.bio)

    wrapper.unmount()
  })

  it('折叠项里有真实的许可与隐私内容', async () => {
    const wrapper = await mountAbout()

    const headers = wrapper.findAll('.el-collapse-item__header').map((item) => item.text())
    expect(headers).toEqual(['许可协议', '隐私政策', '开源仓库', '更新日志'])

    const contents = wrapper.findAll('.el-collapse-item__content').map((item) => item.text())
    expect(contents[0]).toContain('MIT')
    expect(contents[1]).toContain(coreZhCN.about.privacy.content)
    // 仓库与更新日志折叠项不是占位文案，各自带一个可点的入口。
    expect(contents[2]).toContain('github.com/xiaobo2010/zvp5')
    // 版本公告已并入站内通知：这里指向通知收件箱，而不是不存在的 /changelog。
    expect(contents[3]).toContain('站内通知')

    wrapper.unmount()
  })

  it('外部链接指向仓库与 LICENSE，并带 noopener', async () => {
    const wrapper = await mountAbout()

    const links = wrapper.findAll('a.about-link')
    expect(links.length).toBe(2)
    expect(links[0].attributes('href')).toBe('https://github.com/xiaobo2010/zvp5/blob/main/LICENSE')
    expect(links[1].attributes('href')).toBe('https://github.com/xiaobo2010/zvp5')
    for (const link of links) {
      expect(link.attributes('target')).toBe('_blank')
      expect(link.attributes('rel')).toBe('noopener noreferrer')
    }

    wrapper.unmount()
  })

  it('更新日志入口指向站内通知，而不是不存在的 /changelog', async () => {
    const wrapper = await mountAbout()

    await wrapper.get('.about-link--button').trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/notifications')

    await wrapper.get('[data-action="changelog"]').trigger('click')
    expect(pushMock).toHaveBeenCalledTimes(2)
    expect(pushMock).not.toHaveBeenCalledWith('/changelog')

    wrapper.unmount()
  })
})
