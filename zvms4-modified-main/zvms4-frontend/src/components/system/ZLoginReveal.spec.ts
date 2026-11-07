import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ZLoginReveal from './ZLoginReveal.vue'

describe('ZLoginReveal', () => {
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('渲染问候与身份，并在时间轴结束后通知父级', () => {
    vi.useFakeTimers()
    const wrapper = mount(ZLoginReveal, {
      props: { name: '张三', role: '管理员' },
      attachTo: document.body
    })

    const text = (document.body.textContent ?? '').replace(/\s+/g, '')
    expect(text).toContain('你好，张三')
    expect(text).toContain('身份：管理员')
    expect(text).toContain('ZVMS')
    expect(wrapper.emitted('done')).toBeUndefined()

    vi.advanceTimersByTime(2800)
    expect(wrapper.emitted('done')).toHaveLength(1)

    wrapper.unmount()
  })

  it('姓名为空时回退到「同学」', () => {
    const wrapper = mount(ZLoginReveal, {
      props: { name: '  ', role: '学生' },
      attachTo: document.body
    })

    const text = (document.body.textContent ?? '').replace(/\s+/g, '')
    expect(text).toContain('你好，同学')
    expect(text).toContain('身份：学生')

    wrapper.unmount()
  })

  it('姓名为空字符串时回退到「同学」', () => {
    const wrapper = mount(ZLoginReveal, {
      props: { name: '', role: '教师' },
      attachTo: document.body
    })

    expect((document.body.textContent ?? '').replace(/\s+/g, '')).toContain('你好，同学')

    wrapper.unmount()
  })

  it('保留无障碍标签', () => {
    const wrapper = mount(ZLoginReveal, {
      props: { name: '李四', role: '志愿者' },
      attachTo: document.body
    })

    const container = document.body.querySelector('.login-reveal')
    expect(container).not.toBeNull()
    expect(container?.getAttribute('role')).toBe('status')
    expect(container?.getAttribute('aria-live')).toBe('polite')
    expect(container?.getAttribute('aria-label')).toContain('你好，李四')
    expect(container?.getAttribute('aria-label')).toContain('身份：志愿者')

    wrapper.unmount()
  })

  it('reduced-motion 模式下提前结束并跳过复杂动画', () => {
    // Mock matchMedia to return reduced motion preference
    const originalMatchMedia = window.matchMedia
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn()
    }))

    vi.useFakeTimers()
    const wrapper = mount(ZLoginReveal, {
      props: { name: '王五', role: '管理员' },
      attachTo: document.body
    })

    const container = document.body.querySelector('.login-reveal')
    expect(container?.classList.contains('is-reduced')).toBe(true)

    // reduced-motion 模式下应在 1000ms 触发 done
    expect(wrapper.emitted('done')).toBeUndefined()
    vi.advanceTimersByTime(1000)
    expect(wrapper.emitted('done')).toHaveLength(1)

    wrapper.unmount()
    window.matchMedia = originalMatchMedia
  })

  it('组件卸载时清理所有定时器', () => {
    vi.useFakeTimers()
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    const wrapper = mount(ZLoginReveal, {
      props: { name: '测试', role: '用户' },
      attachTo: document.body
    })

    wrapper.unmount()

    // 应该清理至少 4 个定时器：done / slice / particle / cleanup
    expect(clearTimeoutSpy).toHaveBeenCalledTimes(4)

    clearTimeoutSpy.mockRestore()
  })
})
