import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import UserPage from './UserPage.vue'
import api from '@/api'

const { updateBannedMock, readOneV3Mock, notificationMock, confirmMock } = vi.hoisted(() => ({
  updateBannedMock: vi.fn(),
  readOneV3Mock: vi.fn(),
  notificationMock: vi.fn(),
  confirmMock: vi.fn()
}))

// axios 链路会拉起真实 router（401 跳转），这里用空实现挡住，避免测试里创建真实路由实例。
vi.mock('@/router', () => ({
  default: { push: vi.fn(), replace: vi.fn(), currentRoute: { value: { path: '/' } } }
}))

vi.mock('@/api', () => ({
  default: {
    user: {
      readOne_v3: readOneV3Mock,
      updateBanned: updateBannedMock
    }
  }
}))

// 路由只用到 params 和 replace/back;封禁按钮点了不该触发任何跳转断言,给最小实现。
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '20240001', action: 'info' }, fullPath: '/user/20240001/info' }),
  useRouter: () => ({ replace: vi.fn(), back: vi.fn(), push: vi.fn() })
}))

// 页面靠 position 判权限,靠 _id 判「不能封禁自己」——管理员查看他人即可通过两条守卫。
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ _id: 'admin-oid', position: ['admin'], class_id: '' })
}))

// 保留 vue-i18n 的真实导出（createI18n 等）：i18n/index.ts 在模块加载时就会用它们建实例，
// 这里只把 useI18n 换成确定性实现。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: unknown) =>
        params === undefined || typeof params === 'string'
          ? key
          : `${key} ${JSON.stringify(params)}`
    })
  }
})

vi.mock('@/components', () => ({
  ZActivityList: { name: 'ZActivityList', template: '<div class="activity-list-stub" />' }
}))
vi.mock('@/components/activity/ZActivityMember.vue', () => ({
  default: { name: 'ZActivityMember', template: '<div class="activity-member-stub" />' }
}))
vi.mock('@/components/group/ZUserModification.vue', () => ({
  default: { name: 'ZUserModification', template: '<div class="user-modification-stub" />' }
}))
vi.mock('@/components/log/ZLogList.vue', () => ({
  default: { name: 'ZLogList', template: '<div class="log-list-stub" />' }
}))
vi.mock('@icon-park/vue-next', async () => {
  const { defineComponent, h } = await import('vue')
  const stub = (name: string) =>
    defineComponent({ name, setup: () => () => h('span', { class: `icon-${name}` }) })
  return { Info: stub('Info'), Log: stub('Log'), ViewList: stub('ViewList') }
})

// element-plus 整包 mock:真实包会引入 theme-chalk 的 .css,node ESM 下无法加载
// (同 ZBanRules.spec.ts / ServerSsh.spec.ts 的做法)。只需要页面用到的组件,桩要能交互。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElPageHeader = defineComponent({
    name: 'ElPageHeader',
    emits: ['back'],
    setup(_, { slots }) {
      return () => h('header', { class: 'el-page-header-stub' }, [slots.content?.(), slots.extra?.()])
    }
  })

  const ElSegmented = defineComponent({
    name: 'ElSegmented',
    props: { modelValue: String, options: { type: Array, default: () => [] } },
    setup() {
      return () => h('div', { class: 'el-segmented-stub' })
    }
  })

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, text: Boolean, bg: Boolean, disabled: Boolean, loading: Boolean },
    emits: ['click'],
    setup(_, { emit, slots }) {
      return () =>
        h('button', { class: 'el-button-stub', onClick: () => emit('click') }, slots.default?.())
    }
  })

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String },
    setup(_, { slots }) {
      return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
    }
  })

  const ElTooltip = defineComponent({
    name: 'ElTooltip',
    props: { content: String, disabled: Boolean, placement: String },
    setup(_, { slots }) {
      return () => h('span', { class: 'el-tooltip-stub' }, slots.default?.())
    }
  })

  const ElAlert = defineComponent({
    name: 'ElAlert',
    props: { type: String, title: String, closable: Boolean },
    setup(props, { slots }) {
      return () =>
        h('div', { class: 'el-alert-stub', 'data-type': props.type }, [
          h('span', { class: 'el-alert-stub__title' }, props.title),
          slots.default?.()
        ])
    }
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String, width: String },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub', 'data-title': props.title }, [
              slots.default?.(),
              slots.footer?.()
            ])
          : null
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: [String, Number], default: '' },
      type: String,
      placeholder: String,
      rows: Number,
      maxlength: [String, Number],
      showWordLimit: Boolean
    },
    emits: ['update:modelValue', 'blur'],
    setup(props, { emit }) {
      return () => {
        const onInput = (event: Event) =>
          emit('update:modelValue', (event.target as HTMLInputElement).value)
        return props.type === 'textarea'
          ? h('textarea', {
              class: 'el-input-stub',
              value: props.modelValue,
              placeholder: props.placeholder,
              onInput
            })
          : h('input', {
              class: 'el-input-stub',
              value: props.modelValue,
              placeholder: props.placeholder,
              onInput
            })
      }
    }
  })

  return {
    ElPageHeader,
    ElSegmented,
    ElButton,
    ElTag,
    ElTooltip,
    ElAlert,
    ElDialog,
    ElInput,
    ElIcon: { name: 'ElIcon', template: '<span class="el-icon-stub"><slot /></span>' },
    ElLoading: { service: () => ({ close: () => undefined }) },
    ElMessageBox: { confirm: confirmMock },
    ElNotification: notificationMock
  }
})

const userFixture = {
  _id: 'target-oid',
  id: 20240001,
  name: '张三',
  group: ['Z2024-1'],
  past: [],
  roles: ['student'],
  class_id: 'Z2024-1',
  banned: false
}

function mountPage(): VueWrapper {
  return mount(UserPage)
}

const buttonByText = (wrapper: VueWrapper, text: string) =>
  wrapper.findAll('button').find((button) => button.text() === text)

beforeEach(() => {
  readOneV3Mock.mockReset().mockResolvedValue(structuredClone(userFixture))
  updateBannedMock.mockReset().mockResolvedValue(true)
  notificationMock.mockReset()
  confirmMock.mockReset().mockResolvedValue('confirm')
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('opens the ban dialog and submits the ban reason', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await buttonByText(wrapper, 'manage.personalPanel.ban')!.trigger('click')

  const dialog = wrapper.find('.el-dialog-stub')
  expect(dialog.exists()).toBe(true)
  expect(dialog.attributes('data-title')).toBe('manage.personalPanel.banDialogTitle')
  // 需求规定的说明文案必须出现在弹窗里(而不是走 confirm 弹框)
  expect(dialog.text()).toContain('manage.personalPanel.banDialogNotice')

  await dialog.find('.el-input-stub').setValue('同学号多设备登录')
  await buttonByText(wrapper, 'manage.personalPanel.banDialogConfirm')!.trigger('click')
  await flushPromises()

  // 封禁接口在 main 侧多带一个封禁天数参数，这里只断言前三个语义参数
  expect(updateBannedMock.mock.calls[0]?.slice(0, 3)).toEqual([
    '20240001',
    true,
    '同学号多设备登录'
  ])
  expect(notificationMock).toHaveBeenCalledWith({
    title: 'manage.personalPanel.bannedNotification {"name":"张三"}',
    type: 'success'
  })
  // 成功后关闭弹窗并重新拉取用户,让封禁 tag / 封禁信息立即生效
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  expect(readOneV3Mock).toHaveBeenCalledTimes(2)

  wrapper.unmount()
})

it('refuses to submit a blank ban reason', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await buttonByText(wrapper, 'manage.personalPanel.ban')!.trigger('click')
  await buttonByText(wrapper, 'manage.personalPanel.banDialogConfirm')!.trigger('click')
  await flushPromises()

  expect(updateBannedMock).not.toHaveBeenCalled()
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
  expect(wrapper.text()).toContain('manage.personalPanel.banReasonInvalid')

  wrapper.unmount()
})

it('shows the ban reason and permanent status for an already banned user', async () => {
  readOneV3Mock.mockResolvedValue({
    ...structuredClone(userFixture),
    banned: true,
    ban_reason: '同学号多设备登录',
    ban_permanent: true,
    ban_until: null
  })
  const wrapper = mountPage()
  await flushPromises()

  const alert = wrapper.find('.el-alert-stub')
  expect(alert.exists()).toBe(true)
  expect(alert.attributes('data-type')).toBe('error')
  expect(alert.text()).toContain('manage.personalPanel.banReasonLabel：同学号多设备登录')
  expect(alert.text()).toContain('manage.personalPanel.banPermanent')

  wrapper.unmount()
})
