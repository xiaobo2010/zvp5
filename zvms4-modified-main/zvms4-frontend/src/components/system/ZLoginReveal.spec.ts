import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ZLoginReveal from './ZLoginReveal.vue'

describe('ZLoginReveal', () => {
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('逐字渲染问候与身份，并在时间轴结束后通知父级', () => {
    vi.useFakeTimers()
    const wrapper = mount(ZLoginReveal, {
      props: { name: '张三', role: '管理员' },
      attachTo: document.body
    })

    const text = (document.body.textContent ?? '').replace(/\s+/g, '')
    expect(text).toContain('你好，张三')
    expect(text).toContain('身份：管理员')
    expect(wrapper.emitted('done')).toBeUndefined()

    vi.advanceTimersByTime(2820)
    expect(wrapper.emitted('done')).toHaveLength(1)

    wrapper.unmount()
  })

  it('姓名为空时回退到「同学」', () => {
    const wrapper = mount(ZLoginReveal, {
      props: { name: '  ', role: '学生' },
      attachTo: document.body
    })

    expect((document.body.textContent ?? '').replace(/\s+/g, '')).toContain('你好，同学')

    wrapper.unmount()
  })
})
