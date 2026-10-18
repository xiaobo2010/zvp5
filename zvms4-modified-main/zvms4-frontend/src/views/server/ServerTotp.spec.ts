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
        },
        ElProgress: true
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
