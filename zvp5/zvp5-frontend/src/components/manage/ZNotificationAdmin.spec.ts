import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZNotificationAdmin from './ZNotificationAdmin.vue'
import { listAll, updateNotification, deleteNotification } from '@/api/notifications'
import { createChangelog, getChangelog } from '@/api/changelog'
import type { NotificationListData, NotificationRecord } from '@/api/notifications'

const {
  elMessageErrorSpy,
  elMessageSuccessSpy,
  elMessageInfoSpy,
  elMessageBoxConfirmSpy
} = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  elMessageInfoSpy: vi.fn(),
  elMessageBoxConfirmSpy: vi.fn()
}))

vi.mock('@/api/notifications', () => ({
  listAll: vi.fn(),
  createNotification: vi.fn(),
  updateNotification: vi.fn(),
  deleteNotification: vi.fn(),
  listVersions: vi.fn(),
  rollbackNotification: vi.fn(),
  httpStatusOf: () => null
}))

vi.mock('@/api/changelog', () => ({
  getChangelog: vi.fn(),
  createChangelog: vi.fn(),
  updateChangelog: vi.fn(),
  deleteChangelog: vi.fn()
}))

const reauth = vi.hoisted(() => ({ withToken: vi.fn(), cancelled: vi.fn() }))

vi.mock('@/plugins/reauth', () => ({
  withReauth: reauth.withToken,
  isReauthCancelled: reauth.cancelled
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params === undefined ? key : `${key} ${JSON.stringify(params)}`
  })
}))

vi.mock('@/components/markdown', () => ({
  ZMarkdownEditor: { name: 'ZMarkdownEditor', template: '<div class="markdown-editor-stub" />' },
  ZMarkdown: { name: 'ZMarkdown', template: '<div class="markdown-stub" />' }
}))

vi.mock('./ZNotificationHistoryDialog.vue', () => ({
  default: {
    name: 'ZNotificationHistoryDialog',
    props: { notification: Object, visible: Boolean },
    template: '<div class="history-dialog-stub" />'
  }
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, Fragment, h } = await import('vue')

  const ElTable = defineComponent({
    name: 'ElTable',
    props: { data: { type: Array, default: () => [] } },
    setup(props, { slots }) {
      return () => {
        const columns: Array<unknown> = []
        for (const node of slots.default?.() ?? []) {
          if (Array.isArray(node)) {
            columns.push(...node)
          } else if (
            node &&
            typeof node === 'object' &&
            (node as { type?: unknown }).type === Fragment
          ) {
            columns.push(...((node as { children: unknown[] }).children ?? []))
          } else {
            columns.push(node)
          }
        }
        const defs = columns
          .filter(
            (node) =>
              node &&
              typeof node === 'object' &&
              (node as { type?: { name?: string } }).type?.name === 'ElTableColumn'
          )
          .map(
            (node) =>
              node as {
                props: Record<string, unknown>
                children: Record<string, unknown>
              }
          )
          .map((node) => ({ props: node.props ?? {}, slots: node.children ?? {} }))
        const renderCell = (
          def: { props: Record<string, unknown>; slots: Record<string, unknown> },
          row: Record<string, unknown>
        ) => {
          const slot = def.slots?.default
          if (typeof slot === 'function') {
            return slot({ row })
          }
          const prop = def.props?.prop
          return typeof prop === 'string' ? (row[prop] ?? '') : ''
        }
        const rows = props.data as Array<Record<string, unknown>>
        return h('table', { class: 'el-table-stub' }, [
          h('thead', h('tr', defs.map((def) => h('th', String(def.props?.label ?? ''))))),
          h(
            'tbody',
            rows.map((row) =>
              h(
                'tr',
                { class: 'el-table-stub__row' },
                defs.map((def) => h('td', [renderCell(def, row)]))
              )
            )
          ),
          rows.length === 0 ? h('div', { class: 'el-table-stub__empty' }, slots.empty?.()) : null
        ])
      }
    }
  })

  const ElTableColumn = defineComponent({
    name: 'ElTableColumn',
    props: { prop: String, label: String },
    setup() {
      return () => null
    }
  })

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, size: String, loading: Boolean, disabled: Boolean },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || undefined,
            onClick: () => {
              if (props.disabled) {
                return
              }
              emit('click')
            }
          },
          slots.default?.()
        )
    }
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub', 'data-title': props.title }, [
              ...(slots.default?.() ?? []),
              ...(slots.footer?.() ?? [])
            ])
          : null
    }
  })

  const ElForm = defineComponent({
    name: 'ElForm',
    props: { model: Object, rules: Object },
    setup(_, { slots, expose }) {
      expose({
        validate: async () => true,
        clearValidate: () => undefined,
        resetFields: () => undefined
      })
      return () => h('form', { class: 'el-form-stub' }, slots.default?.())
    }
  })

  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    props: { prop: String, label: String },
    setup(_, { slots }) {
      return () => h('div', { class: 'el-form-item-stub' }, slots.default?.())
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: [String, Number], default: '' },
      placeholder: String,
      type: String
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElEmpty = defineComponent({
    name: 'ElEmpty',
    props: { description: String },
    setup(props) {
      return () => h('div', { class: 'el-empty-stub' }, props.description)
    }
  })

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String },
    setup(_, { slots }) {
      return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
    }
  })

  return {
    ElCard: { name: 'ElCard', template: '<section><slot name="header" /><slot /></section>' },
    ElTable,
    ElTableColumn,
    ElButton,
    ElDialog,
    ElForm,
    ElFormItem,
    ElInput,
    ElEmpty,
    ElTag,
    ElIcon: { name: 'ElIcon', template: '<i><slot /></i>' },
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: elMessageInfoSpy
    }),
    ElMessageBox: Object.assign(vi.fn(), {
      confirm: elMessageBoxConfirmSpy
    })
  }
})

const record: NotificationRecord = {
  id: 'n-1',
  title: '开学通知',
  content: '本周一升旗仪式改到操场。',
  version: 1,
  content_hash: 'hash-1',
  author_id: 'admin-oid',
  author_name: '管理员',
  created_at: '2026-10-01T08:00:00',
  updated_at: '2026-10-01T08:00:00',
  active: true
}

const listData: NotificationListData = { version: 1, notifications: [record] }

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(label))
}

beforeEach(() => {
  vi.mocked(listAll).mockReset()
  vi.mocked(updateNotification).mockReset()
  vi.mocked(deleteNotification).mockReset()
  vi.mocked(getChangelog).mockReset()
  vi.mocked(createChangelog).mockReset()
  reauth.withToken.mockReset()
  reauth.cancelled.mockReset()
  reauth.withToken.mockImplementation(
    async (_reason: string, action: (token: string) => Promise<unknown>) => action('verify-token')
  )
  reauth.cancelled.mockImplementation((error: unknown) => error === 'cancel')
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageInfoSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')
  vi.mocked(listAll).mockResolvedValue({ version: 1, notifications: [{ ...record }] })
  vi.mocked(getChangelog).mockResolvedValue([])
  vi.mocked(deleteNotification).mockResolvedValue()
})

describe('通知管理写操作的密码再认证', () => {
  it('删除通知先过再认证，令牌过期后换一张重试成功', async () => {
    reauth.withToken.mockImplementation(
      async (_reason: string, action: (token: string) => Promise<unknown>) => {
        try {
          return await action('stale-token')
        } catch {
          return action('fresh-token')
        }
      }
    )
    vi.mocked(deleteNotification)
      .mockRejectedValueOnce(Object.assign(new Error('forbidden'), { status: 403 }))
      .mockResolvedValueOnce()

    const wrapper = mount(ZNotificationAdmin)
    await flushPromises()

    await findButton(wrapper, 'manage.notifications.delete')!.trigger('click')
    await flushPromises()

    expect(reauth.withToken).toHaveBeenCalledWith(
      'manage.notifications.reauthReason',
      expect.any(Function)
    )
    expect(vi.mocked(deleteNotification).mock.calls[0]).toEqual(['n-1', 'stale-token'])
    expect(vi.mocked(deleteNotification).mock.calls[1]).toEqual(['n-1', 'fresh-token'])
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.notifications.deletedNotice')
    expect(elMessageErrorSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('取消密码框时不删除，只给一条信息提示', async () => {
    reauth.withToken.mockRejectedValue('cancel')

    const wrapper = mount(ZNotificationAdmin)
    await flushPromises()

    await findButton(wrapper, 'manage.notifications.delete')!.trigger('click')
    await flushPromises()

    expect(deleteNotification).not.toHaveBeenCalled()
    expect(elMessageInfoSpy).toHaveBeenCalledWith('manage.reauth.cancelled')
    expect(elMessageSuccessSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('停用通知同样带令牌，成功后替换行状态', async () => {
    vi.mocked(updateNotification).mockResolvedValue({
      notification: { ...record, active: false },
      version: 2
    })

    const wrapper = mount(ZNotificationAdmin)
    await flushPromises()

    await findButton(wrapper, 'manage.notifications.deactivate')!.trigger('click')
    await flushPromises()

    expect(vi.mocked(updateNotification).mock.calls[0]).toEqual([
      'n-1',
      { active: false },
      'verify-token'
    ])
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.notifications.deactivatedNotice')

    wrapper.unmount()
  })

  it('changelog 新增先过再认证再提交', async () => {
    vi.mocked(createChangelog).mockResolvedValue()

    const wrapper = mount(ZNotificationAdmin)
    await flushPromises()

    await findButton(wrapper, 'manage.notifications.changelog.add')!.trigger('click')
    const dialogs = wrapper.findAll('.el-dialog-stub')
    const dialog = dialogs[dialogs.length - 1]!
    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('v4.3.0-8')
    await inputs[1]!.setValue('2026-10-28')

    const saveButton = dialog
      .findAll('button')
      .find((button) => button.text() === 'manage.notifications.changelog.dialog.save')
    expect(saveButton).toBeDefined()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(reauth.withToken).toHaveBeenCalledWith(
      'manage.notifications.changelog.reauthReason',
      expect.any(Function)
    )
    expect(vi.mocked(createChangelog).mock.calls[0]).toEqual([
      { version: 'v4.3.0-8', date: '2026-10-28', items: [] },
      'verify-token'
    ])

    wrapper.unmount()
  })
})

describe('版本自动公告不混进人工通知列表', () => {
  it('source_ref 非空的条目被过滤，只留人写的公告', async () => {
    vi.mocked(listAll).mockResolvedValue({
      version: 2,
      notifications: [
        {
          ...record,
          id: 'n-2',
          title: 'v4.3.0 版本更新',
          author_name: 'SYSTEM',
          source_ref: '0c00000000000000000000aa'
        },
        { ...record }
      ]
    })

    const wrapper = mount(ZNotificationAdmin)
    await flushPromises()

    expect(wrapper.findAll('.el-table-stub__row')).toHaveLength(1)
    expect(wrapper.text()).toContain('开学通知')
    expect(wrapper.text()).not.toContain('v4.3.0 版本更新')

    wrapper.unmount()
  })
})
