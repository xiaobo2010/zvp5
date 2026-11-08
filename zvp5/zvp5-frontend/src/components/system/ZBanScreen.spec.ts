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

  it('没有 ackText 时不渲染确认按钮', () => {
    const wrapper = mountBan()
    expect(query('.z-ban__ack')).toBeNull()
    wrapper.unmount()
  })

  it('点击确认按钮触发 ack', async () => {
    const wrapper = mountBan({ ackText: '我知道了' })

    const ack = query<HTMLButtonElement>('.z-ban__ack')
    expect(ack).not.toBeNull()
    expect(ack?.textContent?.trim()).toBe('我知道了')

    ack?.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('ack')).toHaveLength(1)

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
})
