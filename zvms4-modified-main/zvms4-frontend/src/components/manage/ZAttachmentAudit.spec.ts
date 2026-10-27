import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ZAttachmentAudit from './ZAttachmentAudit.vue'
import {
  decideAttachment,
  getAttachmentAudit,
  listAuditAttachments
} from '@/api/attachment/audit'
import { getBytes, getThumb } from '@/api/attachment/read'
import { deleteOne } from '@/api/attachment/manage'
import type { AttachmentAuditDetail, AttachmentAuditListItem } from '@/../types'

const { elMessageErrorSpy, elMessageSuccessSpy, elMessageBoxConfirmSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  elMessageBoxConfirmSpy: vi.fn()
}))

vi.mock('@/api/attachment/audit', () => ({
  listAuditAttachments: vi.fn(),
  getAttachmentAudit: vi.fn(),
  decideAttachment: vi.fn()
}))

vi.mock('@/api/attachment/read', () => ({
  getBytes: vi.fn(),
  getThumb: vi.fn()
}))

vi.mock('@/api/attachment/manage', () => ({
  deleteOne: vi.fn()
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params === undefined ? key : `${key} ${JSON.stringify(params)}`
  })
}))

// element-plus 整包 mock:真实包会连带引入 theme-chalk 的 .css,node ESM 下无法加载。
// 表格/表单桩做成可交互的最小实现,让用例能真的驱动筛选与行操作。
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
    props: {
      type: String,
      size: String,
      loading: Boolean,
      disabled: Boolean,
      text: Boolean,
      plain: Boolean
    },
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

  const ElCard = defineComponent({
    name: 'ElCard',
    setup(_, { slots }) {
      return () =>
        h('section', { class: 'el-card-stub' }, [
          slots.header ? h('header', { class: 'el-card-stub__header' }, slots.header()) : null,
          h('div', { class: 'el-card-stub__body' }, slots.default?.())
        ])
    }
  })

  const ElDrawer = defineComponent({
    name: 'ElDrawer',
    props: { modelValue: Boolean, title: String, size: String, direction: String },
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-drawer-stub', 'data-title': props.title }, [
              ...(slots.default?.() ?? []),
              ...(slots.footer?.() ?? [])
            ])
          : null
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: [String, Number], default: '' }, placeholder: String },
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

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: { type: [String, Number], default: '' }, placeholder: String },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-select-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  // 时间范围桩:输入一次即回一个固定区间,便于断言 created_from/created_to。
  const ElDatePicker = defineComponent({
    name: 'ElDatePicker',
    props: { modelValue: { type: Array, default: undefined } },
    emits: ['update:modelValue'],
    setup(_, { emit }) {
      return () =>
        h('input', {
          class: 'el-date-picker-stub',
          value: '',
          onInput: () =>
            emit('update:modelValue', [
              new Date('2026-10-01T00:00:00.000Z'),
              new Date('2026-10-02T00:00:00.000Z')
            ])
        })
    }
  })

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: [String, Number] },
    setup() {
      return () => null
    }
  })

  const ElEmpty = defineComponent({
    name: 'ElEmpty',
    props: { description: String },
    setup(props, { slots }) {
      return () =>
        h('div', { class: 'el-empty-stub' }, [props.description, ...(slots.default?.() ?? [])])
    }
  })

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String, effect: String },
    setup(_, { slots }) {
      return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
    }
  })

  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('span', { class: 'el-icon-stub' }, slots.default?.())
    }
  })

  const ElTooltip = defineComponent({
    name: 'ElTooltip',
    props: { content: String },
    setup(_, { slots }) {
      return () => h('span', { class: 'el-tooltip-stub' }, slots.default?.())
    }
  })

  return {
    ElTable,
    ElTableColumn,
    ElButton,
    ElCard,
    ElDrawer,
    ElInput,
    ElSelect,
    ElDatePicker,
    ElOption,
    ElEmpty,
    ElTag,
    ElIcon,
    ElTooltip,
    // v-loading 由 unplugin-vue-components 的 ElementPlusResolver 从本包按需引入
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: Object.assign(vi.fn(), {
      confirm: elMessageBoxConfirmSpy
    })
  }
})

vi.mock('@/components/form/ZSelectPerson.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZSelectPerson',
      props: { modelValue: { type: [String, Array], default: '' } },
      emits: ['update:modelValue'],
      setup(props, { emit }) {
        return () =>
          h('input', {
            class: 'z-select-person-stub',
            value: typeof props.modelValue === 'string' ? props.modelValue : '',
            onInput: (event: Event) =>
              emit('update:modelValue', (event.target as HTMLInputElement).value)
          })
      }
    })
  }
})

vi.mock('@/components/utils/ZPagination.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZPagination',
      props: {
        total: { type: Number, default: 0 },
        currentPage: { type: Number, default: 1 },
        pageSize: { type: Number, default: 20 }
      },
      emits: ['update:currentPage', 'update:pageSize'],
      setup(props, { emit }) {
        return () =>
          h(
            'div',
            {
              class: 'z-pagination-stub',
              'data-total': props.total,
              'data-page-size': props.pageSize,
              'data-current-page': props.currentPage,
              onClick: () => emit('update:currentPage', props.currentPage + 1)
            },
            ''
          )
      }
    })
  }
})

const rowFixture: AttachmentAuditListItem = {
  _id: '69b7a4ad0243d973f1e1a2c1',
  kind: 'original',
  mime: 'image/jpeg',
  size_bytes: 204800,
  width: 1200,
  height: 800,
  purpose: 'volunteer_order',
  original_name: '登记单.jpg',
  entity_type: 'activity',
  entity_oid: '69b7a4ad0243d973f1e1a2c9',
  status: 'ready',
  ocr_status: 'done',
  created_at: '2026-10-20T08:00:00',
  owner_oid: '69b7a4ad0243d973f1e1a2c2',
  has_ocr: true
}

const detailFixture: AttachmentAuditDetail = {
  ...rowFixture,
  owner_name: '张三',
  owner_id: '20240801',
  ocr_text: '学号 姓名 时长\n20240801 张三 2h',
  lines: [
    {
      text: '20240801 张三 2h',
      confidence: 0.92,
      box: { left: 10, top: 20, width: 200, height: 30 }
    }
  ],
  events: [
    {
      _id: 'event-1',
      action: 'upload',
      created_at: '2026-10-20T08:00:00'
    }
  ]
}

function mockApi(options?: { rows?: AttachmentAuditListItem[]; total?: number; listRejects?: boolean }) {
  vi.mocked(listAuditAttachments).mockReset()
  vi.mocked(getAttachmentAudit).mockReset()
  vi.mocked(decideAttachment).mockReset()
  vi.mocked(getBytes).mockReset()
  vi.mocked(getThumb).mockReset()
  vi.mocked(deleteOne).mockReset()

  if (options?.listRejects) {
    vi.mocked(listAuditAttachments).mockRejectedValue(new Error('boom'))
  } else {
    vi.mocked(listAuditAttachments).mockResolvedValue({
      data: options?.rows ? structuredClone(options.rows) : [structuredClone(rowFixture)],
      total: options?.total ?? (options?.rows?.length ?? 1)
    })
  }
  vi.mocked(getAttachmentAudit).mockResolvedValue(structuredClone(detailFixture))
  vi.mocked(decideAttachment).mockResolvedValue(undefined)
  vi.mocked(getBytes).mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }))
  vi.mocked(getThumb).mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }))
  vi.mocked(deleteOne).mockResolvedValue(undefined)
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')
}

function mountPage(): VueWrapper {
  return mount(ZAttachmentAudit)
}

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

beforeEach(() => {
  mockApi()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('lists attachments with the default query and renders each row', async () => {
  const wrapper = mountPage()
  await flushPromises()

  expect(listAuditAttachments).toHaveBeenCalledWith({
    page: 1,
    size: 20,
    purpose: undefined,
    status: undefined,
    // 默认只列原图：缩略图与派生文件是原图的附属行
    kind: 'original',
    ocrStatus: undefined,
    ownerOid: undefined,
    createdFrom: undefined,
    createdTo: undefined
  })

  const table = wrapper.find('.el-table-stub')
  expect(table.findAll('.el-table-stub__row').length).toBe(1)
  expect(table.text()).toContain('登记单.jpg')
  expect(table.text()).toContain('image/jpeg')
  expect(table.text()).toContain('200.0 KB')
  expect(table.text()).toContain('ready')
  expect(table.text()).toContain('activity')

  wrapper.unmount()
})

it('sends the chosen filters to the list endpoint', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const selects = wrapper.findAll('.el-select-stub')
  await selects[0]!.setValue('volunteer_order')
  await flushPromises()
  await selects[1]!.setValue('verified')
  await flushPromises()
  await selects[2]!.setValue('done')
  await flushPromises()
  await selects[3]!.setValue('original')
  await flushPromises()

  expect(listAuditAttachments).toHaveBeenLastCalledWith(
    expect.objectContaining({
      purpose: 'volunteer_order',
      status: 'verified',
      ocrStatus: 'done',
      kind: 'original'
    })
  )

  await wrapper.find('.z-select-person-stub').setValue('owner-oid-1')
  await flushPromises()
  expect(listAuditAttachments).toHaveBeenLastCalledWith(
    expect.objectContaining({ ownerOid: 'owner-oid-1' })
  )

  await wrapper.find('.el-date-picker-stub').setValue('2026-10-01')
  await flushPromises()
  expect(listAuditAttachments).toHaveBeenLastCalledWith(
    expect.objectContaining({
      createdFrom: '2026-10-01T00:00:00.000Z',
      createdTo: '2026-10-02T00:00:00.000Z'
    })
  )

  wrapper.unmount()
})

it('shows the empty state when nothing matches the filters', async () => {
  mockApi({ rows: [], total: 0 })
  const wrapper = mountPage()
  await flushPromises()

  const empty = wrapper.find('.el-empty-stub')
  expect(empty.exists()).toBe(true)
  expect(empty.text()).toContain('manage.attachments.empty')
  expect(wrapper.findAll('.el-table-stub__row').length).toBe(0)

  wrapper.unmount()
})

it('shows the error state and a single toast when the list request fails', async () => {
  mockApi({ listRejects: true })
  const wrapper = mountPage()
  await flushPromises()

  const empty = wrapper.find('.el-empty-stub')
  expect(empty.exists()).toBe(true)
  expect(empty.text()).toContain('manage.attachments.loadFailed')
  expect(elMessageErrorSpy).toHaveBeenCalledTimes(1)
  expect(elMessageErrorSpy).toHaveBeenCalledWith('manage.attachments.loadFailed')

  wrapper.unmount()
})

it('re-queries with the next page when the pagination changes', async () => {
  mockApi({ rows: [rowFixture], total: 45 })
  const wrapper = mountPage()
  await flushPromises()

  const pagination = wrapper.find('.z-pagination-stub')
  expect(pagination.attributes('data-total')).toBe('45')

  await pagination.trigger('click')
  await flushPromises()

  expect(listAuditAttachments).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

  wrapper.unmount()
})

it('opens the audit drawer and renders OCR lines plus the event trail', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.attachments.table.view')!.trigger('click')
  await flushPromises()

  expect(getAttachmentAudit).toHaveBeenCalledWith(rowFixture._id)
  const drawer = wrapper.find('.el-drawer-stub')
  expect(drawer.exists()).toBe(true)
  expect(drawer.text()).toContain('张三')
  expect(drawer.text()).toContain('20240801 张三 2h')
  expect(drawer.text()).toContain('upload')

  wrapper.unmount()
})

it('decides an attachment through the PATCH status contract', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.attachments.table.view')!.trigger('click')
  await flushPromises()

  await wrapper.find('.el-input-stub').setValue('材料齐全')
  await findButton(wrapper, 'manage.attachments.detail.approve')!.trigger('click')
  await flushPromises()

  expect(decideAttachment).toHaveBeenCalledWith(rowFixture._id, {
    status: 'verified',
    note: '材料齐全'
  })
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.attachments.detail.approved')

  wrapper.unmount()
})

it('asks for a confirmation before rejecting an attachment', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.attachments.table.view')!.trigger('click')
  await flushPromises()

  await findButton(wrapper, 'manage.attachments.detail.reject')!.trigger('click')
  await flushPromises()

  expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
  expect(decideAttachment).toHaveBeenCalledWith(rowFixture._id, {
    status: 'rejected',
    note: undefined
  })

  wrapper.unmount()
})

it('does not decide when the rejection confirmation is cancelled', async () => {
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.attachments.table.view')!.trigger('click')
  await flushPromises()

  elMessageBoxConfirmSpy.mockRejectedValue('cancel')
  await findButton(wrapper, 'manage.attachments.detail.reject')!.trigger('click')
  await flushPromises()

  expect(decideAttachment).not.toHaveBeenCalled()

  wrapper.unmount()
})

it('deletes an attachment only after the confirmation and refreshes the list', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const callsBefore = vi.mocked(listAuditAttachments).mock.calls.length
  await findButton(wrapper, 'manage.attachments.table.delete')!.trigger('click')
  await flushPromises()

  expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
  expect(deleteOne).toHaveBeenCalledWith(rowFixture._id)
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.attachments.delete.deleted')
  expect(vi.mocked(listAuditAttachments).mock.calls.length).toBeGreaterThan(callsBefore)

  wrapper.unmount()
})

it('does not delete when the confirmation is cancelled', async () => {
  const wrapper = mountPage()
  await flushPromises()

  elMessageBoxConfirmSpy.mockRejectedValue('cancel')
  await findButton(wrapper, 'manage.attachments.table.delete')!.trigger('click')
  await flushPromises()

  expect(deleteOne).not.toHaveBeenCalled()
  expect(elMessageSuccessSpy).not.toHaveBeenCalled()

  wrapper.unmount()
})
