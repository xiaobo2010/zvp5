import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import ZLoginReveal from './ZLoginReveal.vue'

function mountReveal(props: {
  name: string
  role: string
  active?: boolean
  prewarm?: boolean
}) {
  return mount(ZLoginReveal, {
    props,
    attachTo: document.body,
    global: { plugins: [i18n] }
  })
}

describe('ZLoginReveal', () => {
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('渲染问候与身份，并在时间轴结束后通知父级', () => {
    vi.useFakeTimers()
    const wrapper = mountReveal({ name: '张三', role: '管理员' })

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
    const wrapper = mountReveal({ name: '  ', role: '学生' })

    const text = (document.body.textContent ?? '').replace(/\s+/g, '')
    expect(text).toContain('你好，同学')
    expect(text).toContain('身份：学生')

    wrapper.unmount()
  })

  it('姓名为空字符串时回退到「同学」', () => {
    const wrapper = mountReveal({ name: '', role: '教师' })

    expect((document.body.textContent ?? '').replace(/\s+/g, '')).toContain('你好，同学')

    wrapper.unmount()
  })

  it('保留无障碍标签', () => {
    const wrapper = mountReveal({ name: '李四', role: '志愿者' })

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
    const wrapper = mountReveal({ name: '王五', role: '管理员' })

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

    const wrapper = mountReveal({ name: '测试', role: '用户' })

    wrapper.unmount()

    // 应该清理至少 4 个定时器：done / slice / particle / cleanup
    expect(clearTimeoutSpy).toHaveBeenCalledTimes(4)

    clearTimeoutSpy.mockRestore()
  })

  it('active=false 时不触发 done，active 变为 true 后才触发', async () => {
    vi.useFakeTimers()
    const wrapper = mountReveal({ name: '张三', role: '管理员', active: false })

    const container = document.body.querySelector('.login-reveal')
    expect(container?.classList.contains('is-waiting')).toBe(true)

    // 等待 2800ms，仍不触发 done
    vi.advanceTimersByTime(2800)
    expect(wrapper.emitted('done')).toBeUndefined()

    // 设置 active=true
    await wrapper.setProps({ active: true })
    expect(container?.classList.contains('is-waiting')).toBe(false)

    // 再等 2800ms，应该触发 done
    vi.advanceTimersByTime(2800)
    expect(wrapper.emitted('done')).toHaveLength(1)

    wrapper.unmount()
  })

  it('active=false 时已渲染不透明幕布', () => {
    const wrapper = mountReveal({ name: '张三', role: '管理员', active: false })

    const container = document.body.querySelector('.login-reveal')
    expect(container).not.toBeNull()
    expect(container?.classList.contains('is-waiting')).toBe(true)

    // 幕布面板应该存在
    const panels = document.body.querySelectorAll('.login-reveal__panel')
    expect(panels.length).toBe(2)

    wrapper.unmount()
  })

  it('reduced-motion + active=false 时根节点不透明，active 后 1000ms 触发 done', async () => {
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
    const wrapper = mountReveal({ name: '王五', role: '管理员', active: false })

    const container = document.body.querySelector('.login-reveal')
    expect(container?.classList.contains('is-reduced')).toBe(true)
    expect(container?.classList.contains('is-waiting')).toBe(true)

    // active 前不触发 done
    vi.advanceTimersByTime(1000)
    expect(wrapper.emitted('done')).toBeUndefined()

    // 设置 active=true
    await wrapper.setProps({ active: true })
    expect(container?.classList.contains('is-waiting')).toBe(false)

    // reduced-motion 模式 1000ms 后触发 done
    vi.advanceTimersByTime(1000)
    expect(wrapper.emitted('done')).toHaveLength(1)

    wrapper.unmount()
    window.matchMedia = originalMatchMedia
  })

  it('active 从 true 开始时保持默认行为', async () => {
    vi.useFakeTimers()
    const wrapper = mountReveal({ name: '张三', role: '管理员', active: true })

    await wrapper.vm.$nextTick()

    const container = document.body.querySelector('.login-reveal')
    expect(container?.classList.contains('is-waiting')).toBe(false)

    vi.advanceTimersByTime(2800)
    expect(wrapper.emitted('done')).toHaveLength(1)

    wrapper.unmount()
  })

  it('prewarm 模式只渲染不可见实例，不启动时间线也不读屏', () => {
    vi.useFakeTimers()
    const wrapper = mountReveal({
      name: '张三',
      role: '管理员',
      active: false,
      prewarm: true
    })

    const container = document.body.querySelector('.login-reveal')
    expect(container?.classList.contains('is-prewarm')).toBe(true)
    expect(container?.getAttribute('aria-hidden')).toBe('true')
    expect(container?.hasAttribute('role')).toBe(false)

    vi.advanceTimersByTime(2800)
    expect(wrapper.emitted('done')).toBeUndefined()

    wrapper.unmount()
  })
})
