import { describe, expect, it } from 'vitest'
import { AI_WRITE_ACTIONS, buildAiMessages, stripWrappingFence } from './aiPrompts'

describe('Markdown AI 提示词', () => {
  it('system 约束只回正文，user 带上原文', () => {
    const messages = buildAiMessages({ action: 'polish', source: '  原文内容  ' })

    expect(messages).toHaveLength(2)
    expect(messages[0].role).toBe('system')
    expect(messages[0].content).toContain('只输出改写后的正文')
    expect(messages[1].role).toBe('user')
    expect(messages[1].content).toContain('润色')
    expect(messages[1].content).toContain('原文内容')
  })

  it('额外要求紧跟在操作说明之后', () => {
    const user = buildAiMessages({
      action: 'shorten',
      source: '一段话',
      instruction: '  保留专有名词  '
    })[1].content

    expect(user).toContain('额外要求：保留专有名词')
    expect(user.indexOf('精简')).toBeLessThan(user.indexOf('额外要求'))
  })

  it('原文为空时说明按额外要求生成，而不是塞一段空引用', () => {
    const user = buildAiMessages({ action: 'continue', source: '   ' })[1].content

    expect(user).toContain('原文为空')
    expect(user).not.toContain('原文：\n')
  })

  it('五种操作都有自己的提示词', () => {
    const prompts = AI_WRITE_ACTIONS.map(
      (action) => buildAiMessages({ action, source: '内容' })[1].content
    )

    expect(new Set(prompts).size).toBe(AI_WRITE_ACTIONS.length)
  })
})

describe('剥掉模型自作主张的围栏', () => {
  it('整段被 ``` 包住时剥掉', () => {
    expect(stripWrappingFence('```\n正文\n```')).toBe('正文')
  })

  it('带语言的围栏也剥', () => {
    expect(stripWrappingFence('```markdown\n正文\n```')).toBe('正文')
  })

  it('原文本身是代码块时不动', () => {
    const source = '说明\n```python\nprint(1)\n```'
    expect(stripWrappingFence(source)).toBe(source)
  })

  it('没有围栏时原样返回', () => {
    expect(stripWrappingFence('普通文本')).toBe('普通文本')
  })
})
