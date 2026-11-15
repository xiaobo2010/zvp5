import { defineComponent, h, reactive, ref } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { coreZhCN } from '@/i18n/locales/core'
import { loadNotificationState, resetAccountNotificationState } from '@/utils/notificationState'
import { readMyDevice, setMyPrimaryDevice } from '@/api/user/device'
import { listActive } from '@/api/notifications'
import type { MyDeviceStatus } from '@/api/user/device'
import UserProfile from './UserProfile.vue'

/**
 * 「我的」页此前只剩账号信息，脚本里 loadDevice / setMyPrimaryDevice 等全是死代码。
 * 这组用例锁住补回来的两块：
 *   1. 主设备卡片按状态给出绑定 / 更换 / 解绑，且解绑、更换必须走二次校验；
 *   2. 通知栏的未读计数与已读标记（本地偏好）真的生效。
 */

const { storeState, successSpy, errorSpy, warningSpy, confirmSpy, pushMock, backMock } = vi.hoisted(
  () => ({
    storeState: {
      _id: 'user-1',
      id: 20240001,
      name: '张三',
      position: ['student'] as string[],
      class_id: '',
      isLogin: true,
      banned: false,
      resetPassword: vi.fn()
    },
    successSpy: vi.fn(),
    errorSpy: vi.fn(),
    warningSpy: vi.fn(),
    confirmSpy: vi.fn(),
    pushMock: vi.fn(),
    backMock: vi.fn()
  })
)

vi.mock('@/stores/user', () => ({
  useUserStore: () => reactive(storeState)
}))

vi.mock('@/stores/groups', () => ({
  useGroupsStore: () => ({ fetchGroup: vi.fn(async () => null) })
}))

vi.mock('@/stores/header', () => ({
  useHeaderStore: () => ({ setHeader: vi.fn() })
}))

vi.mock('@/api/notifications', () => ({
  listActive: vi.fn()
}))

vi.mock('@/api/user/device', () => ({
  readMyDevice: vi.fn(),
  setMyPrimaryDevice: vi.fn(),
  clearMyPrimaryDevice: vi.fn()
}))

vi.mock('@/plugins/short-token', () => ({
  withTemporaryToken: vi.fn(async (_id: string, _reason: string, run: (token: string) => unknown) =>
    run('temp-token')
  ),
  isTemporaryTokenCancelled: () => false
}))

vi.mock('@/utils/theme', () => ({
  useTheme: () => ({ dark: ref(false), toggleTheme: vi.fn() })
}))

vi.mock('@/views/user/modifyPasswordDialogs', () => ({ modifyPasswordDialogs: vi.fn() }))

vi.mock('@/components/markdown/ZMarkdown.vue', () => ({
  default: defineComponent({
    name: 'ZMarkdown',
    props: { source: String },
    setup: (props) => () => h('div', { class: 'z-markdown-stub' }, props.source)
  })
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: vi.fn(), push: pushMock, back: backMock })
}))

// element-plus 整包 mock：真实包会引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup:
      (_, { slots }) =>
      () =>
        h('span', { class: 'el-icon-stub' }, slots.default?.())
  })

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: { modelValue: Boolean },
    emits: ['update:modelValue'],
    setup: () => () => h('span', { class: 'el-switch-stub' })
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub' }, [
              h('h2', { class: 'el-dialog-stub__title' }, props.title),
              h('div', { class: 'el-dialog-stub__body' }, slots.default?.()),
              slots.footer?.()
            ])
          : null
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: String, placeholder: String },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-stub',
          value: props.modelValue ?? '',
          placeholder: props.placeholder,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  return {
    ElIcon,
    ElSwitch,
    ElDialog,
    ElInput,
    ElMessage: Object.assign(vi.fn(), {
      success: successSpy,
      error: errorSpy,
      warning: warningSpy,
      info: vi.fn()
    }),
    ElMessageBox: Object.assign(vi.fn(), { confirm: confirmSpy })
  }
})

function makeStatus(overrides: Partial<MyDeviceStatus> = {}): MyDeviceStatus {
  return {
    xuehai_id: '1001',
    primary_xuehai_id: null,
    device_banned: false,
    device_ban_reason: null,
    device_ban_until: null,
    ...overrides
  }
}

function notice(overrides: Record<string, unknown> = {}) {
  return {
    id: 'notice-1',
    title: '活动调整',
    content: '本周活动改到周六。',
    version: 2,
    author_id: 'author-1',
    author_name: '张老师',
    created_at: '2026-10-21T09:30:00',
    updated_at: '2026-10-21T10:15:00',
    active: true,
    ...overrides
  }
}

function buildI18n() {
  return createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': coreZhCN } })
}

const mounted: VueWrapper[] = []

async function mountProfile(): Promise<VueWrapper> {
  const wrapper = mount(UserProfile, { global: { plugins: [buildI18n()] } })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text() === text)
}

beforeEach(() => {
  localStorage.clear()
  resetAccountNotificationState('user-1')
  storeState.position = ['student']
  storeState.banned = false
  successSpy.mockClear()
  errorSpy.mockClear()
  warningSpy.mockClear()
  confirmSpy.mockReset().mockResolvedValue('confirm')
  pushMock.mockReset()
  backMock.mockReset()
  vi.mocked(readMyDevice).mockReset().mockResolvedValue(makeStatus())
  vi.mocked(setMyPrimaryDevice).mockReset().mockResolvedValue(undefined)
  vi.mocked(listActive).mockReset().mockResolvedValue({ version: 1, notifications: [] })
})

afterEach(() => {
  while (mounted.length > 0) {
    mounted.pop()?.unmount()
  }
  localStorage.clear()
})

describe('UserProfile 主设备卡片', () => {
  it('未绑定时显示绑定按钮，绑定成功后刷新状态', async () => {
    vi.mocked(readMyDevice)
      .mockResolvedValueOnce(makeStatus())
      .mockResolvedValue(makeStatus({ primary_xuehai_id: '1001' }))

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain(coreZhCN.device.profile.unboundHint)
    const bind = buttonByText(wrapper, coreZhCN.device.profile.bind)
    expect(bind).toBeDefined()

    await bind!.trigger('click')
    await flushPromises()

    // 首次绑定不需要二次校验：后端只接受「在本机发起」。
    expect(setMyPrimaryDevice).toHaveBeenCalledWith('1001')
    expect(successSpy).toHaveBeenCalledWith(coreZhCN.device.profile.bindSuccess)
    expect(readMyDevice).toHaveBeenCalledTimes(2)
  })

  it('本机是主设备时可以更换，更换写入新设备号', async () => {
    vi.mocked(readMyDevice)
      .mockResolvedValueOnce(makeStatus({ primary_xuehai_id: '1001' }))
      .mockResolvedValue(makeStatus({ primary_xuehai_id: '2002' }))

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain(coreZhCN.device.profile.isPrimary)

    await buttonByText(wrapper, coreZhCN.device.profile.change)!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    const input = wrapper.get('.el-input-stub')
    await input.setValue('2002')
    await buttonByText(wrapper, coreZhCN.device.profile.changeSubmit)!.trigger('click')
    await flushPromises()

    expect(setMyPrimaryDevice).toHaveBeenCalledWith('2002', 'temp-token')
    expect(successSpy).toHaveBeenCalledWith(coreZhCN.device.profile.changeSuccess)
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  })

  it('本机不是主设备时更换与解绑都不可点，并给出只读原因', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(makeStatus({ primary_xuehai_id: '9999' }))

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain(coreZhCN.device.profile.otherDevice)
    expect(wrapper.text()).toContain(coreZhCN.device.profile.notPrimaryHint)
    expect(
      buttonByText(wrapper, coreZhCN.device.profile.change)!.attributes('disabled')
    ).toBeDefined()
    expect(
      buttonByText(wrapper, coreZhCN.device.profile.unbind)!.attributes('disabled')
    ).toBeDefined()
  })

  it('设备封禁时把原因与解除时间写清楚', async () => {
    vi.mocked(readMyDevice).mockResolvedValue(
      makeStatus({
        device_banned: true,
        device_ban_reason: '违规刷时长',
        device_ban_until: '2026-12-01T00:00:00'
      })
    )

    const wrapper = await mountProfile()

    const banner = wrapper.get('.profile-device-ban')
    expect(banner.text()).toContain(coreZhCN.device.profile.bannedTitle)
    expect(banner.text()).toContain('违规刷时长')
    expect(banner.text()).toContain('解除时间')
  })

  it('读取失败时保留重试入口', async () => {
    vi.mocked(readMyDevice)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(makeStatus({ primary_xuehai_id: '1001' }))

    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain(coreZhCN.device.profile.failed)
    await buttonByText(wrapper, coreZhCN.device.profile.retry)!.trigger('click')
    await flushPromises()

    expect(readMyDevice).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain(coreZhCN.device.profile.isPrimary)
  })
})

describe('UserProfile 通知栏', () => {
  it('显示未读条数，展开后把这条标记为已读', async () => {
    vi.mocked(listActive).mockResolvedValue({
      version: 3,
      notifications: [
        notice({ id: 'n1', title: '未读通知' }),
        notice({ id: 'n2', title: '已读通知' })
      ]
    })

    const wrapper = await mountProfile()

    expect(wrapper.get('.profile-notices-head .yg2-badge').text()).toBe('2 条未读')
    expect(wrapper.findAll('.profile-notice-dot.is-unread')).toHaveLength(2)

    await wrapper.findAll('.profile-notice-head')[0].trigger('click')
    await flushPromises()

    expect(wrapper.get('.profile-notices-head .yg2-badge').text()).toBe('1 条未读')
    expect(wrapper.findAll('.profile-notice-dot.is-unread')).toHaveLength(1)
    // 「读到哪一版」写进账号共享的已读映射（真源在服务端），刷新后不会再弹回未读。
    expect(loadNotificationState('user-1').read.n1).toBe(2)
  })

  it('没有通知时给出空态而不是空白卡片', async () => {
    const wrapper = await mountProfile()

    expect(wrapper.text()).toContain('暂时没有新通知')
    expect(wrapper.find('.profile-notice-list').exists()).toBe(false)
  })
})

describe('UserProfile 快捷操作', () => {
  it('更新日志入口指向站内通知，且不显示裸 i18n key', async () => {
    const wrapper = await mountProfile()

    const entry = buttonByText(wrapper, coreZhCN.profile.actions.changelog)
    expect(entry).toBeDefined()
    // 缺失的 key 会原样渲染成 profile.actions.changelog，这里锁住它已补齐。
    expect(wrapper.text()).not.toContain('profile.actions.changelog')

    await entry!.trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/notifications')
    expect(pushMock).not.toHaveBeenCalledWith('/changelog')
  })
})
