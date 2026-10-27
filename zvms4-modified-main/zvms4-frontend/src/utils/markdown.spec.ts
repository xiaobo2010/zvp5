import { describe, expect, it } from 'vitest'
import { renderMarkdown, renderMarkdownInline } from './markdown'

/**
 * Markdown 渲染工具。
 *
 * 守住两件事：表格能整张渲染出来（对齐用 align，不放开 style），
 * 以及净化白名单没有被放松（脚本、事件属性、javascript: 链接都进不来）。
 */

describe('markdown 渲染', () => {
  it('表格整张留下来', () => {
    const html = renderMarkdown('| 项目 | 数量 |\n| --- | --- |\n| 活动 | 3 |')

    expect(html).toContain('<table>')
    expect(html).toContain('<thead>')
    expect(html).toContain('<tbody>')
    expect(html).toContain('<th>项目</th>')
    expect(html).toContain('<td>3</td>')
  })

  it('表格对齐用 align 属性，不放开 style', () => {
    const html = renderMarkdown('| 左 | 中 | 右 |\n| :--- | :---: | ---: |\n| a | b | c |')

    expect(html).toContain('align="left"')
    expect(html).toContain('align="center"')
    expect(html).toContain('align="right"')
    expect(html).not.toContain('style=')
  })

  it('原始 HTML 不进 DOM：脚本、事件属性、标签都没了', () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n<iframe src="//evil"></iframe>'
    )

    expect(html).not.toContain('<script')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('<iframe')
  })

  it('javascript: 链接不会被渲染成可点链接', () => {
    const html = renderMarkdown('[点我](javascript:alert(1))')

    expect(html).not.toContain('href="javascript:')
  })

  it('链接、行内代码与代码块都在', () => {
    const html = renderMarkdown('见 [文档](https://example.com/a) 与 `code`\n\n```js\nconst a = 1\n```')

    expect(html).toContain('<a href="https://example.com/a">文档</a>')
    expect(html).toContain('<code>code</code>')
    // 代码块保留换行。
    expect(html).toContain('<pre><code>const a = 1\n</code></pre>')
    // 语言标记是 class：白名单里没有它，净化时被剥掉。
    expect(html).not.toContain('language-js')
  })

  it('行内渲染只出行内标签', () => {
    const html = renderMarkdownInline('**粗体**')

    expect(html).toContain('<strong>粗体</strong>')
    expect(html).not.toContain('<p>')
  })

  it('空输入返回空串', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown('   ')).toBe('')
    expect(renderMarkdown(null)).toBe('')
    expect(renderMarkdownInline(undefined)).toBe('')
  })
})
