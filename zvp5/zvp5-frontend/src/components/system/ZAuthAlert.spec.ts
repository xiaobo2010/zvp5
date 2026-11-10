import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ZAuthAlert from './ZAuthAlert.vue'

type AlertProps = InstanceType<typeof ZAuthAlert>['$props']

const baseProps = {
  title: '登录失败',
  message: '用户名或密码不正确，请重新输入。'
}

function mountAlert(props: Partial<AlertProps> = {}) {
  return mount(ZAuthAlert, {
    props: { ...baseProps, ...props },
    attachTo: document.body
  })
}

function query<T extends Element = HTMLElement>(selector: string): T | null {
  return document.body.querySelector<T>(selector)
}

function mockReducedMotion(): () => void {
  const original = window.matchMedia
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
  return () => {
    window.matchMedia = original
  }
}

describe('ZAuthAlert', () => {
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
    document.body.style.overflow = ''
  })

  it('传送到 body 并带 alertdialog 无障碍属性', () => {
    const wrapper = mountAlert()

    const root = query('.z-alert')
    expect(root).not.toBeNull()
    expect(root?.parentElement).toBe(document.body)
    expect(root?.getAttribute('role')).toBe('alertdialog')
    expect(root?.getAttribute('aria-modal')).toBe('true')

    wrapper.unmount()
  })

  it('aria-labelledby 与 aria-describedby 指向 title/message 节点', () => {
    const wrapper = mountAlert()

    const root = query('.z-alert')
    const titleId = root?.getAttribute('aria-labelledby') ?? ''
    const messageId = root?.getAttribute('aria-describedby') ?? ''

    expect(titleId).not.toBe('')
    expect(messageId).not.toBe('')
    expect(document.getElementById(titleId)?.textContent).toBe(baseProps.title)
    expect(document.getElementById(messageId)?.textContent).toBe(baseProps.message)

    wrapper.unmount()
  })

  it('渲染标题、正文与可选补充说明', () => {
    const wrapper = mountAlert({ hint: '连续 5 次失败后账号将被锁定 10 分钟' })

    expect(query('.z-alert__title')?.textContent).toBe(baseProps.title)
    expect(query('.z-alert__message')?.textContent).toBe(baseProps.message)
    expect(query('.z-alert__hint')?.textContent).toContain('连续 5 次失败')

    wrapper.unmount()
  })

  it('没有 hint 时不渲染补充说明节点', () => {
    const wrapper = mountAlert()

    expect(query('.z-alert__hint')).toBeNull()

    wrapper.unmount()
  })

  it('danger 与 warning 变体的类名与主色变量不同', () => {
    const danger = mountAlert({ variant: 'danger' })
    const dangerRoot = query('.z-alert')
    expect(dangerRoot?.classList.contains('z-alert--danger')).toBe(true)
    const dangerAccent = dangerRoot?.style.getPropertyValue('--z-alert-accent') ?? ''
    danger.unmount()

    const warning = mountAlert({ variant: 'warning' })
    const warningRoot = query('.z-alert')
    expect(warningRoot?.classList.contains('z-alert--warning')).toBe(true)
    const warningAccent = warningRoot?.style.getPropertyValue('--z-alert-accent') ?? ''
    warning.unmount()

    expect(dangerAccent).toContain('--yg-red-500')
    expect(warningAccent).toContain('--yg-orange-500')
    expect(dangerAccent).not.toBe(warningAccent)
  })

  it('默认使用 danger 变体', () => {
    const wrapper = mountAlert()

    expect(query('.z-alert')?.classList.contains('z-alert--danger')).toBe(true)

    wrapper.unmount()
  })

  it('没有 ackText/actionText 时不渲染按钮', () => {
    const wrapper = mountAlert()

    expect(query('.z-alert__btn')).toBeNull()
    expect(query('.z-alert__actions')).toBeNull()

    wrapper.unmount()
  })

  it('有文案时渲染按钮并触发对应事件', async () => {
    const wrapper = mountAlert({ ackText: '返回', actionText: '重新登录' })

    const ghost = query<HTMLButtonElement>('.z-alert__btn--ghost')
    const solid = query<HTMLButtonElement>('.z-alert__btn--solid')
    expect(ghost?.textContent?.trim()).toBe('返回')
    expect(solid?.textContent?.trim()).toBe('重新登录')

    ghost?.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('ack')).toHaveLength(1)
    expect(wrapper.emitted('action')).toBeUndefined()

    solid?.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('action')).toHaveLength(1)

    wrapper.unmount()
  })

  it('挂载后聚焦主按钮，没有主按钮时聚焦次要按钮', async () => {
    const withAction = mountAlert({ ackText: '返回', actionText: '重新登录' })
    await withAction.vm.$nextTick()
    await withAction.vm.$nextTick()
    expect(document.activeElement?.classList.contains('z-alert__btn--solid')).toBe(true)
    withAction.unmount()

    const withAck = mountAlert({ ackText: '返回' })
    await withAck.vm.$nextTick()
    await withAck.vm.$nextTick()
    expect(document.activeElement?.classList.contains('z-alert__btn--ghost')).toBe(true)
    withAck.unmount()
  })

  it('既无按钮时聚焦对话框本身', async () => {
    const wrapper = mountAlert()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(document.activeElement?.classList.contains('z-alert')).toBe(true)

    wrapper.unmount()
  })

  it('Esc 不会关闭对话框，也不会触发任何事件', async () => {
    const wrapper = mountAlert({ ackText: '返回', actionText: '重新登录' })
    await wrapper.vm.$nextTick()

    const root = query('.z-alert')
    root?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))

    expect(query('.z-alert')).not.toBeNull()
    expect(wrapper.emitted('ack')).toBeUndefined()
    expect(wrapper.emitted('action')).toBeUndefined()

    wrapper.unmount()
  })

  it('reduced-motion 时添加 is-reduced', () => {
    const restore = mockReducedMotion()

    const wrapper = mountAlert()
    expect(query('.z-alert')?.classList.contains('is-reduced')).toBe(true)

    wrapper.unmount()
    restore()
  })

  it('锁定 body 滚动并在卸载时还原', () => {
    document.body.style.overflow = 'auto'
    const wrapper = mountAlert()
    expect(document.body.style.overflow).toBe('hidden')

    wrapper.unmount()
    expect(document.body.style.overflow).toBe('auto')
  })
})
