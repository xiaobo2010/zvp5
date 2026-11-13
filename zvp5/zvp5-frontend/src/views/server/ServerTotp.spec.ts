import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ServerTotp from './ServerTotp.vue'
import { getMyTotp } from '@/api/system/totp.ts'

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: vi.fn() })
}))

vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => ({
    _id: '000000000000000000000001',
    position: ['admin']
  })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('@/api/system/totp.ts', () => ({
  getMyTotp: vi.fn()
}))

const NOW = new Date('2026-10-18T00:00:00.000Z')

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  vi.mocked(getMyTotp).mockResolvedValue({
    data: {
      status: 'ok',
      code: 200,
      data: { otp: '123456', exp: Math.floor(NOW.getTime() / 1000) + 1 }
    }
  } as never)
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

it('keeps the refresh entry visible after the code expires', async () => {
  const wrapper = mount(ServerTotp, {
    global: {
      stubs: {
        ElCard: {
          template: '<section><slot name="header" /><slot /></section>'
        },
        ElButton: {
          template: '<button><slot /></button>'
        }
      }
    }
  })

  await flushPromises()
  expect(wrapper.text()).not.toContain('server.totp.loading')

  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()

  expect(wrapper.text()).toContain('server.totp.expired')
  expect(wrapper.text()).toContain('server.totp.refresh')
  expect(wrapper.text()).not.toContain('server.totp.loading')
})

it('counts down inside the hexagon instead of a progress bar', async () => {
  vi.mocked(getMyTotp).mockResolvedValue({
    data: {
      status: 'ok',
      code: 200,
      data: { otp: '123456', exp: Math.floor(NOW.getTime() / 1000) + 60 }
    }
  } as never)

  const wrapper = mount(ServerTotp, {
    global: {
      stubs: {
        ElCard: { template: '<section><slot name="header" /><slot /></section>' },
        ElButton: { template: '<button><slot /></button>' }
      }
    }
  })
  await flushPromises()

  // 旧的进度条彻底退役：模板里不再有 el-progress
  expect(wrapper.find('.el-progress').exists()).toBe(false)

  const hex = wrapper.find('.phi-srv-hex')
  expect(hex.exists()).toBe(true)
  expect(hex.classes()).toContain('phi-srv-hex--progress')

  // 六边形内部有填充层，角度由 --phi-hex-p 驱动
  const fill = hex.find('.phi-srv-hex__progress')
  expect(fill.exists()).toBe(true)

  const readProgress = () =>
    Number(String(fill.attributes('style') ?? '').match(/--phi-hex-p:\s*([\d.]+)%/)?.[1])

  const start = readProgress()
  expect(start).toBeGreaterThan(90)

  // 走掉 30 秒后，填充角度必须跟着收缩
  await vi.advanceTimersByTimeAsync(30_000)
  await flushPromises()
  expect(readProgress()).toBeLessThan(start)

  // 读数本身仍是剩余秒数
  expect(hex.find('.phi-srv-hex__value').text()).toBe('30')
})
