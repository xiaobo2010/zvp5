import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import ZBanScreen from './ZBanScreen.vue'

type BanProps = InstanceType<typeof ZBanScreen>['$props']

const baseProps = {
  title: '账号已被封禁',
  reason: '多次提交虚假义工记录',
  until: '解封时间：2026-12-31 23:59'
}

function mountBan(props: Partial<BanProps> = {}) {
  return mount(ZBanScreen, {
    props: { ...baseProps, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] }
  })
}

function query<T extends Element = HTMLElement>(selector: string): T | null {
  return document.body.querySelector<T>(selector)
}

describe('ZBanScreen', () => {
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
    document.body.style.overflow = ''
  })

  it('传送到 body 并带有 alertdialog 无障碍属性', () => {
    const wrapper = mountBan()

    const root = query('.z-ban')
    expect(root).not.toBeNull()
    expect(root?.parentElement).toBe(document.body)
    expect(root?.getAttribute('role')).toBe('alertdialog')
    expect(root?.getAttribute('aria-modal')).toBe('true')

    const titleId = root?.getAttribute('aria-labelledby') ?? ''
    const reasonId = root?.getAttribute('aria-describedby') ?? ''
    expect(document.getElementById(titleId)?.textContent).toContain(baseProps.title)
    expect(document.getElementById(reasonId)?.textContent).toContain(baseProps.reason)

    wrapper.unmount()
  })

  it('渲染原因、期限与申诉文案', () => {
    const wrapper = mountBan({ appeal: '如有异议请联系管理员' })

    expect(query('.z-ban__reason')?.textContent).toBe(baseProps.reason)
    const text = document.body.textContent ?? ''
    expect(text).toContain(baseProps.until)
    expect(text).toContain('如有异议请联系管理员')

    wrapper.unmount()
  })

  it('风险格默认全亮，device 变体与 riskLevel 可调整', async () => {
    const wrapper = mountBan()
    expect(document.body.querySelectorAll('.z-ban__cell')).toHaveLength(12)
    expect(document.body.querySelectorAll('.z-ban__cell.is-lit')).toHaveLength(12)

    await wrapper.setProps({ variant: 'device' })
    expect(document.body.querySelectorAll('.z-ban__cell.is-lit')).toHaveLength(10)

    await wrapper.setProps({ riskLevel: 40 })
    expect(document.body.querySelectorAll('.z-ban__cell.is-lit')).toHaveLength(12)

    wrapper.unmount()
  })

  it('countdown 为 0 时隐藏倒计时', () => {
    const wrapper = mountBan()
    expect(query('.z-ban__countdown')).toBeNull()
    wrapper.unmount()
  })

  it('countdown 大于 0 时显示文案与进度比例', async () => {
    const wrapper = mountBan({ countdown: 5, countdownTotal: 10, countdownText: '5 秒后退出' })

    expect(query('.z-ban__countdown')).not.toBeNull()
    expect(query('.z-ban__count')?.textContent).toBe('5 秒后退出')
    expect(query('.z-ban__bar-fill')?.style.transform).toBe('scaleX(0.5)')

    await wrapper.setProps({ countdown: 3, countdownText: '' })
    expect(query('.z-ban__count')?.textContent).toBe('3s')

    wrapper.unmount()
  })

  it('默认锁定 body 滚动并在卸载时还原', () => {
    document.body.style.overflow = 'auto'
    const wrapper = mountBan()
    expect(document.body.style.overflow).toBe('hidden')

    wrapper.unmount()
    expect(document.body.style.overflow).toBe('auto')
  })

  it('lockScroll 为 false 时不改动 body 滚动', () => {
    document.body.style.overflow = 'scroll'
    const wrapper = mountBan({ lockScroll: false })
    expect(document.body.style.overflow).toBe('scroll')

    wrapper.unmount()
    expect(document.body.style.overflow).toBe('scroll')
  })

  it('reduced-motion 时添加 is-reduced', () => {
    const originalMatchMedia = window.matchMedia
    window.matchMedia = vi.fn().mockImplementation((q: string) => ({
      matches: q === '(prefers-reduced-motion: reduce)',
      media: q,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn()
    }))

    const wrapper = mountBan()
    expect(query('.z-ban')?.classList.contains('is-reduced')).toBe(true)

    wrapper.unmount()
    window.matchMedia = originalMatchMedia
  })

  it('默认 15 秒后自动触发消散并 emit ack', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan()

    // 初始状态：未消散
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(false)
    expect(wrapper.emitted('ack')).toBeUndefined()

    // 前进到 15 秒：开始消散
    vi.advanceTimersByTime(15000)
    await wrapper.vm.$nextTick()
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)
    expect(wrapper.emitted('ack')).toBeUndefined()

    // 再前进 1200ms（完整动画时长）：emit ack
    vi.advanceTimersByTime(1200)
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('ack')).toHaveLength(1)

    wrapper.unmount()
  })

  it('autoDismiss 为 0 时立即进入消散', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan({ autoDismiss: 0 })

    // 挂载后立即消散
    await wrapper.vm.$nextTick()
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)

    // 前进完整动画时长后 emit ack
    vi.advanceTimersByTime(1200)
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('ack')).toHaveLength(1)

    wrapper.unmount()
  })

  it('自定义 autoDismiss 按指定时间退场', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan({ autoDismiss: 3 })

    // 前进到 3 秒
    vi.advanceTimersByTime(3000)
    await wrapper.vm.$nextTick()
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)

    // 再前进 1200ms
    vi.advanceTimersByTime(1200)
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('ack')).toHaveLength(1)

    wrapper.unmount()
  })

  it('消散时生成粒子（非 reduced-motion）', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan()

    // 触发消散
    vi.advanceTimersByTime(15000)
    await wrapper.vm.$nextTick()

    // 粒子容器应该存在
    const particleContainer = query('.z-ban__particles')
    expect(particleContainer).not.toBeNull()

    // 粒子应该生成
    const particles = document.body.querySelectorAll('.z-ban__particle')
    expect(particles.length).toBeGreaterThan(0)

    wrapper.unmount()
  })

  it('reduced-motion 消散时不生成粒子且快速淡出', async () => {
    const originalMatchMedia = window.matchMedia
    window.matchMedia = vi.fn().mockImplementation((q: string) => ({
      matches: q === '(prefers-reduced-motion: reduce)',
      media: q,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn()
    }))

    vi.useFakeTimers()
    const wrapper = mountBan()

    // 触发消散
    vi.advanceTimersByTime(15000)
    await wrapper.vm.$nextTick()

    // is-dismissing 和 is-reduced 都应该存在
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)
    expect(query('.z-ban')?.classList.contains('is-reduced')).toBe(true)

    // 不应该有粒子
    expect(query('.z-ban__particles')).toBeNull()

    // 300ms 后 emit ack（而非 1200ms）
    vi.advanceTimersByTime(300)
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('ack')).toHaveLength(1)

    wrapper.unmount()
    window.matchMedia = originalMatchMedia
  })

  it('卸载前清理计时器', () => {
    vi.useFakeTimers()
    const wrapper = mountBan()

    wrapper.unmount()

    // 卸载后前进时间不应触发任何事件
    vi.advanceTimersByTime(20000)
    expect(wrapper.emitted('ack')).toBeUndefined()
  })

  it('只读挂载时的 autoDismiss，prop 后续变化不重置计时', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan({ autoDismiss: 5 })

    // 前进 2 秒后更新 prop
    vi.advanceTimersByTime(2000)
    await wrapper.setProps({ autoDismiss: 10 })

    // 再前进 3 秒（挂载后总共 5 秒）：应触发消散
    vi.advanceTimersByTime(3000)
    await wrapper.vm.$nextTick()
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)

    wrapper.unmount()
  })

  it('非有限 autoDismiss 回退到默认 15 秒', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan({ autoDismiss: NaN })

    // 前进到 15 秒
    vi.advanceTimersByTime(15000)
    await wrapper.vm.$nextTick()
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)

    wrapper.unmount()
  })

  it('负数 autoDismiss 按 0 处理（立即消散）', async () => {
    vi.useFakeTimers()
    const wrapper = mountBan({ autoDismiss: -5 })

    await wrapper.vm.$nextTick()
    expect(query('.z-ban')?.classList.contains('is-dismissing')).toBe(true)

    wrapper.unmount()
  })
})
