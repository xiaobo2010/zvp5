import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import ManageHome from './ManageHome.vue'

/**
 * 管理页分区可见性：
 * - 附件管理对 admin 与 department（学生会）同时开放，与后端 ``_is_manager`` 对齐；
 * - 其它分区（角色/通知/封禁/设置）仍只给 admin；
 * - 团支书仍然只能看「用户」，普通学生整页挡掉。
 */

const { storeState, routeState, replaceSpy, backSpy } = vi.hoisted(() => ({
  storeState: { position: ['department'] as string[], class_id: '' },
  routeState: { params: {} as Record<string, unknown> },
  replaceSpy: vi.fn(),
  backSpy: vi.fn()
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ replace: replaceSpy, back: backSpy })
}))

vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => storeState
}))

vi.mock('@vueuse/core', async () => {
  const { ref: makeRef } = await import('vue')
  const width = makeRef(800)
  const height = makeRef(600)
  return { useWindowSize: () => ({ width, height }) }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const ElPageHeader = defineComponent({
    name: 'ElPageHeader',
    setup(_, { slots }) {
      return () => h('header', { class: 'el-page-header-stub' }, slots.content?.())
    }
  })
  const ElSegmented = defineComponent({
    name: 'ElSegmented',
    props: { modelValue: String, options: { type: Array, default: () => [] } },
    setup() {
      return () => h('div', { class: 'el-segmented-stub' })
    }
  })
  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('span', { class: 'el-icon-stub' }, slots.default?.())
    }
  })
  return { ElPageHeader, ElSegmented, ElIcon }
})

vi.mock('@element-plus/icons-vue', async () => {
  const { defineComponent, h, markRaw } = await import('vue')
  const Icon = markRaw(defineComponent({ name: 'IconStub', setup: () => () => h('i') }))
  return { ArrowLeft: Icon, Avatar: Icon, Bell: Icon }
})

vi.mock('@icon-park/vue-next', async () => {
  const { defineComponent, h, markRaw } = await import('vue')
  const Icon = markRaw(defineComponent({ name: 'IconParkStub', setup: () => () => h('i') }))
  return { Block: Icon, Config: Icon, Group: Icon, Log: Icon, Picture: Icon, User: Icon }
})

vi.mock('@/components/group/ZGroupUserList.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZGroupUserList',
      setup: () => () => h('div', { class: 'stub-ZGroupUserList' })
    })
  }
})
vi.mock('@/components/group/ZGroupList.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZGroupList',
      setup: () => () => h('div', { class: 'stub-ZGroupList' })
    })
  }
})
vi.mock('@/components/log/ZLogList.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZLogList',
      setup: () => () => h('div', { class: 'stub-ZLogList' })
    })
  }
})
vi.mock('@/components/manage/ZBanRules.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZBanRules',
      setup: () => () => h('div', { class: 'stub-ZBanRules' })
    })
  }
})
vi.mock('@/components/manage/ZAttachmentAudit.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZAttachmentAudit',
      setup: () => () => h('div', { class: 'stub-ZAttachmentAudit' })
    })
  }
})
vi.mock('@/components/manage/ZNotificationAdmin.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZNotificationAdmin',
      setup: () => () => h('div', { class: 'stub-ZNotificationAdmin' })
    })
  }
})
vi.mock('@/components/manage/ZSystemSettings.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZSystemSettings',
      setup: () => () => h('div', { class: 'stub-ZSystemSettings' })
    })
  }
})
vi.mock('@/views/manage/ManageRoles.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ManageRoles',
      setup: () => () => h('div', { class: 'stub-ManageRoles' })
    })
  }
})

function mountPage(): VueWrapper {
  return mount(ManageHome)
}

beforeEach(() => {
  storeState.position = ['department']
  storeState.class_id = ''
  routeState.params = { action: 'attachments' }
  replaceSpy.mockReset()
  backSpy.mockReset()
  // jsdom 没有实现 scrollTo：导航自动滚入可视区会用到
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
})

it('lets a department member open the attachments tab', async () => {
  const wrapper = mountPage()
  await flushPromises()

  expect(replaceSpy).not.toHaveBeenCalled()
  expect(wrapper.find('.stub-ZAttachmentAudit').exists()).toBe(true)
  const tabs = wrapper.findAll('.manage-tabs .yg2-seg__item').map((tab) => tab.text())
  expect(tabs.some((label) => label.startsWith('manage.manage.tabs.attachments'))).toBe(true)
  // 其它 admin 分区仍然不给学生会
  expect(tabs.some((label) => label.startsWith('manage.manage.tabs.roles'))).toBe(false)
  expect(tabs.some((label) => label.startsWith('manage.manage.tabs.settings'))).toBe(false)

  wrapper.unmount()
})

it('keeps the admin-only tabs closed for a department member', async () => {
  routeState.params = { action: 'settings' }

  const wrapper = mountPage()
  await flushPromises()

  expect(replaceSpy).toHaveBeenCalledWith('/manage/groups')
  expect(wrapper.find('.stub-ZSystemSettings').exists()).toBe(false)

  wrapper.unmount()
})

it('still blocks a student from the whole manage page', async () => {
  storeState.position = ['student']
  routeState.params = { action: 'attachments' }

  const wrapper = mountPage()
  await flushPromises()

  expect(replaceSpy).toHaveBeenCalledWith('/not-found')
  expect(replaceSpy).toHaveBeenCalledWith('/manage/groups')
  expect(wrapper.find('.stub-ZAttachmentAudit').exists()).toBe(false)

  wrapper.unmount()
})

it('keeps the secretary limited to the users tab', async () => {
  storeState.position = ['secretary']
  storeState.class_id = 'class-1'
  routeState.params = { action: 'attachments' }

  const wrapper = mountPage()
  await flushPromises()

  expect(replaceSpy).toHaveBeenCalledWith('/manage/users')
  expect(wrapper.find('.stub-ZAttachmentAudit').exists()).toBe(false)

  wrapper.unmount()
})

it('still shows every tab to an admin', async () => {
  storeState.position = ['admin']
  routeState.params = { action: 'attachments' }

  const wrapper = mountPage()
  await flushPromises()

  expect(replaceSpy).not.toHaveBeenCalled()
  expect(wrapper.find('.stub-ZAttachmentAudit').exists()).toBe(true)
  const tabs = wrapper.findAll('.manage-tabs .yg2-seg__item').map((tab) => tab.text())
  for (const value of ['roles', 'notifications', 'ban', 'attachments', 'logs', 'settings']) {
    expect(tabs.some((label) => label.startsWith(`manage.manage.tabs.${value}`))).toBe(true)
  }

  wrapper.unmount()
})
