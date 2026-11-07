import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZMarkdown from './ZMarkdown.vue'
import { renderMarkdown, renderMarkdownInline } from '@/utils/markdown.ts'

/**
 * Markdown 渲染组件。
 *
 * 守住四件事：表格不再被削平、超长正文会截断并提示、高频变更只渲染最后一次、
 * 同一段文字再来一次不重复 parse。
 */

vi.mock('@/utils/markdown.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/markdown.ts')>()
  return {
    ...actual,
    renderMarkdown: vi.fn(actual.renderMarkdown),
    renderMarkdownInline: vi.fn(actual.renderMarkdownInline)
  }
})

const renderSpy = vi.mocked(renderMarkdown)
const renderInlineSpy = vi.mocked(renderMarkdownInline)

beforeEach(() => {
  vi.useFakeTimers()
  renderSpy.mockClear()
  renderInlineSpy.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ZMarkdown', () => {
  it('表格整张留下来：表头、表体、单元格都在', () => {
    const wrapper = mount(ZMarkdown, {
      props: { source: '| 项目 | 数量 |\n| --- | --- |\n| 活动 | 3 |' }
    })

    const html = wrapper.html()
    expect(html).toContain('<table>')
    expect(html).toContain('<thead>')
    expect(html).toContain('<tbody>')
    expect(html).toContain('<th>项目</th>')
    expect(html).toContain('<td>活动</td>')

    wrapper.unmount()
  })

  it('表格对齐写成 align，不放开 style', () => {
    const wrapper = mount(ZMarkdown, {
      props: { source: '| 左 | 右 |\n| :--- | ---: |\n| a | b |' }
    })

    const html = wrapper.html()
    expect(html).toContain('align="left"')
    expect(html).toContain('align="right"')
    // style 一律不放行：净化白名单里没有它。
    expect(html).not.toContain('style=')

    wrapper.unmount()
  })

  it('链接与行内代码保留', () => {
    const wrapper = mount(ZMarkdown, {
      props: { source: '见 [文档](https://example.com/a) 与 `code`' }
    })

    const html = wrapper.html()
    expect(html).toContain('<a href="https://example.com/a">文档</a>')
    expect(html).toContain('<code>code</code>')

    wrapper.unmount()
  })

  it('超长正文截断并提示「内容过长已截断」', () => {
    const source = `${'图'.repeat(20_001)}`
    const wrapper = mount(ZMarkdown, { props: { source } })

    expect(wrapper.text()).toContain('内容过长已截断')
    // 只渲染前 20000 个字符，后面的不再进 DOM。
    expect(wrapper.text().split('图').length - 1).toBe(20_000)

    wrapper.unmount()
  })

  it('没超长的正文不提示截断', () => {
    const wrapper = mount(ZMarkdown, { props: { source: '短短一段' } })

    expect(wrapper.text()).not.toContain('内容过长已截断')

    wrapper.unmount()
  })

  it('高频变更只渲染最后一次，不积压', async () => {
    const wrapper = mount(ZMarkdown, { props: { source: 'A' } })
    renderSpy.mockClear()

    await wrapper.setProps({ source: 'B' })
    await wrapper.setProps({ source: 'C' })
    await wrapper.setProps({ source: 'D' })
    await vi.advanceTimersByTimeAsync(200)

    expect(renderSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('D')

    wrapper.unmount()
  })

  it('同一段文字再来一次：命中缓存，不重新 parse', async () => {
    const wrapper = mount(ZMarkdown, { props: { source: 'A' } })
    expect(renderSpy).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ source: 'B' })
    await vi.advanceTimersByTimeAsync(200)
    expect(renderSpy).toHaveBeenCalledTimes(2)

    // 回到 A：内容没变过，不必再 parse 一遍。
    await wrapper.setProps({ source: 'A' })
    await vi.advanceTimersByTimeAsync(200)
    expect(renderSpy).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('A')

    wrapper.unmount()
  })

  it('行内模式走另一条渲染路径', () => {
    const wrapper = mount(ZMarkdown, { props: { source: '**粗体**', inline: true } })

    expect(renderInlineSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.element.tagName).toBe('SPAN')
    expect(wrapper.html()).toContain('<strong>粗体</strong>')

    wrapper.unmount()
  })

  it('immediate：调用方自己节流时不等防抖', async () => {
    const wrapper = mount(ZMarkdown, { props: { source: 'A', immediate: true } })
    renderSpy.mockClear()

    await wrapper.setProps({ source: 'B' })
    await wrapper.vm.$nextTick()

    expect(renderSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('B')

    wrapper.unmount()
  })
})
