import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiQuotaAdmin from './ZAiQuotaAdmin.vue'
import type {
  AiQuotaPage,
  AiQuotaRequestAdminItem,
  AiQuotaSnapshotView,
  AiQuotaUserRow
} from '@/api/ai'

/**
 * 额度管理页。
 *
 * 请求层不 mock：`@/plugins/axios` 才是替身，quota API 按真实路径跑一遍，
 * envelope 拆包与 AiRequestError 映射都在链路里 —— 页面拿到的错误形状与线上一致。
 */

interface AxiosCall {
  method: string
  url: string
  data?: Record<string, unknown>
  params?: Record<string, unknown>
}

const {
  axiosMock,
  elMessageErrorSpy,
  elMessageSuccessSpy,
  elMessageBoxConfirmSpy,
  storeState
} = vi.hoisted(() => ({
  axiosMock: vi.fn<(config: AxiosCall) => Promise<unknown>>(),
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  elMessageBoxConfirmSpy: vi.fn(),
  storeState: { position: ['admin'] as string[] }
}))

vi.mock('@/plugins/axios', () => ({ default: axiosMock, baseURL: '' }))

vi.mock('@/stores/user', () => ({ useUserStore: () => storeState }))

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) =>
        params === undefined ? key : `${key} ${JSON.stringify(params)}`
    })
  }
})

vi.mock('@/components/utils/ZPagination.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZPagination',
      props: { total: Number, currentPage: Number, pageSize: Number, hideOnSinglePage: Boolean },
      setup() {
        return () => h('div', { class: 'z-pagination-stub' })
      }
    })
  }
})

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
            (node) => node as { props: Record<string, unknown>; children: Record<string, unknown> }
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
          h(
            'thead',
            h(
              'tr',
              defs.map((def) => h('th', String(def.props?.label ?? '')))
            )
          ),
          h(
            'tbody',
            rows.map((row) => h('tr', { class: 'el-table-stub__row' }, defs.map((def) => h('td', [renderCell(def, row)]))))
          )
        ])
      }
    }
  })

  const ElTableColumn = defineComponent({
    name: 'ElTableColumn',
    props: { prop: String, label: String, minWidth: [String, Number] },
    setup() {
      return () => null
    }
  })

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, size: String, loading: Boolean, disabled: Boolean, text: Boolean },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || undefined,
            onClick: (event: MouseEvent) => {
              if (props.disabled) {
                return
              }
              emit('click', event)
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
    setup(_, { slots }) {
      return () => h('form', slots.default?.())
    }
  })

  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    props: { label: String },
    setup(props, { slots }) {
      return () => h('div', { class: 'el-form-item-stub' }, [props.label, slots.default?.()])
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: [String, Number], default: '' }, placeholder: String },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('textarea', {
          class: 'el-input-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
        })
    }
  })

  const ElInputNumber = defineComponent({
    name: 'ElInputNumber',
    props: { modelValue: { type: Number, default: 0 }, min: Number, step: Number },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-number-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', Number((event.target as HTMLInputElement).value))
        })
    }
  })

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: { type: [String, Number], default: '' } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-select-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
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

  const ElRadioGroup = defineComponent({
    name: 'ElRadioGroup',
    props: { modelValue: { type: [String, Number, Boolean], default: '' } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-radio-group-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElRadio = defineComponent({
    name: 'ElRadio',
    props: { value: [String, Number], label: String },
    setup(props, { slots }) {
      return () => h('label', { class: 'el-radio-stub' }, [props.label, slots.default?.()])
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
      return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
    }
  })

  return {
    ElTable,
    ElTableColumn,
    ElButton,
    ElDialog,
    ElForm,
    ElFormItem,
    ElInput,
    ElInputNumber,
    ElSelect,
    ElOption,
    ElRadioGroup,
    ElRadio,
    ElTag,
    ElIcon,
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: { confirm: elMessageBoxConfirmSpy }
  }
})

const userRow: AiQuotaUserRow = {
  oid: 'u1',
  name: '张三',
  business_id: '2024001',
  quotas: {
    af: { used: 100, limit: 1000, source: 'explicit' }
  },
  pending_request: true
}

const requestRow: AiQuotaRequestAdminItem = {
  oid: 'req-1',
  period: '202610',
  kind: 'ocr',
  status: 'pending',
  reason: '活动报名要用',
  requested_limit: 800,
  created_at: '2026-10-28 10:00:00',
  decided_at: null,
  decision_note: null,
  user_oid: 'u1',
  user_name: '张三',
  business_id: '2024001'
}

function page<T>(items: T[]): AiQuotaPage<T> {
  return { items, total: items.length, page: 1, size: 20 }
}

function envelope<T>(data: T, status = 200): unknown {
  return { status, data: { status: 'ok', data }, headers: {} }
}

function failure(status: number, detail: unknown): unknown {
  return { status, data: { detail }, headers: {} }
}

let usersResponse: unknown
let requestsResponse: unknown

beforeEach(() => {
  axiosMock.mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')
  storeState.position = ['admin']

  usersResponse = envelope(page([userRow]))
  requestsResponse = envelope(page([requestRow]))

  axiosMock.mockImplementation(async (config: AxiosCall) => {
    if (config.method === 'get' && config.url === '/ai/quota/users') {
      return usersResponse
    }
    if (config.method === 'get' && config.url === '/ai/quota/requests') {
      return requestsResponse
    }
    if (config.method === 'put' && config.url === '/ai/quota/u1') {
      return envelope({ period: '202610', kind: 'af', used: 100, limit: 2000 })
    }
    if (config.method === 'post' && config.url === '/ai/quota/u1/reset') {
      return envelope({ period: '202610', kind: 'af', used: 0, limit: 1000 })
    }
    if (config.method === 'get' && config.url === '/ai/quota/u1') {
      const snapshot: AiQuotaSnapshotView = {
        af: { period: '202610', used: 100, limit: 1000, source: 'explicit' }
      }
      return envelope(snapshot)
    }
    if (config.method === 'post' && config.url.startsWith('/ai/quota/requests/')) {
      return envelope({ request: requestRow, quota: { af: userRow.quotas.af } })
    }
    if (config.method === 'get' && config.url === '/system/role-policies/me') {
      return envelope({
        role: 'student',
        features: { 'quota.manage': false },
        quotas: { af: null, schedule: null },
        updated_at: null,
        updated_by: null
      })
    }
    throw new Error(`unexpected request ${config.method} ${config.url}`)
  })
})

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(ZAiQuotaAdmin)
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((item) => item.text() === text)
}

function dialogButton(dialog: DOMWrapper<Element>, text: string) {
  return dialog.findAll('button').find((item) => item.text() === text)
}

function called(config: Partial<AxiosCall>): boolean {
  return axiosMock.mock.calls.some(([call]) =>
    Object.entries(config).every(([key, value]) => call[key as keyof AxiosCall] === value)
  )
}

function lastCall(): AxiosCall {
  return axiosMock.mock.calls[axiosMock.mock.calls.length - 1]![0]
}

describe('额度管理页', () => {
  it('渲染用户配额表格：姓名、学号、单一 AF 用量与来源徽标', async () => {
    // 后端实际下发的是扁平行（chat/ocr 直接挂行上，id 是学号），这里按真实形状验一遍。
    // chat 与 ocr 是同一个 AF 池的两个别名：页面只出一列，绝不相加。
    usersResponse = envelope({
      items: [
        {
          oid: 'u1',
          name: '张三',
          id: '2024001',
          business_id: '2024001',
          class_id: 'c1',
          period: '202610',
          chat: { period: '202610', used: 100, limit: 1000, source: 'explicit' },
          ocr: { period: '202610', used: 100, limit: 1000, source: 'explicit' },
          pending_request: true
        }
      ],
      total: 1,
      page: 1,
      size: 20
    })
    const wrapper = await mountPage()

    const text = wrapper.text()
    expect(text).toContain('张三')
    expect(text).toContain('2024001')
    expect(text).toContain('100 / 1000')
    // 相加会翻倍，这里不能出现 200 / 2000
    expect(text).not.toContain('200 / 2000')
    expect(text).toContain('ai.quotaAdmin.sourceExplicit')
    expect(text).toContain('ai.quotaAdmin.users.pendingBadge')

    wrapper.unmount()
  })

  it('搜索把姓名/学号关键字带到 query，并回到第一页', async () => {
    const wrapper = await mountPage()

    await wrapper.find('.el-input-stub').setValue('张三')
    await buttonByText(wrapper, 'ai.quotaAdmin.search')!.trigger('click')
    await flushPromises()

    expect(lastCall().url).toBe('/ai/quota/users')
    expect(lastCall().params).toEqual({ page: 1, size: 20, query: '张三' })

    wrapper.unmount()
  })

  it('改上限走 PUT /ai/quota/{oid}，body 是 kind + limit_count', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.quotaAdmin.users.changeAfLimit')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    await wrapper.find('.el-input-number-stub').setValue('2000')
    await buttonByText(wrapper, 'ai.common.save')!.trigger('click')
    await flushPromises()

    expect(called({ method: 'put', url: '/ai/quota/u1' })).toBe(true)
    const putCall = axiosMock.mock.calls.find(([call]) => call.method === 'put')![0]
    expect(putCall.data).toEqual({ kind: 'af', limit_count: 2000 })
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.quotaAdmin.savedNotice')

    wrapper.unmount()
  })

  it('清零用量二次确认后走 POST reset', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.quotaAdmin.users.resetAf')!.trigger('click')
    await flushPromises()

    expect(elMessageBoxConfirmSpy).toHaveBeenCalled()
    expect(called({ method: 'post', url: '/ai/quota/u1/reset' })).toBe(true)
    const resetCall = axiosMock.mock.calls.find(([call]) => call.url === '/ai/quota/u1/reset')![0]
    expect(resetCall.data).toEqual({ kind: 'af' })

    wrapper.unmount()
  })

  it('确认框取消时不发 reset 请求', async () => {
    elMessageBoxConfirmSpy.mockRejectedValue(new Error('cancel'))
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.quotaAdmin.users.resetAf')!.trigger('click')
    await flushPromises()

    expect(called({ url: '/ai/quota/u1/reset' })).toBe(false)

    wrapper.unmount()
  })

  it('审批区渲染申请人、能力、说明与期望额度', async () => {
    // 审批队列的行同样是仓储形状（`_id` + user_name/business_id）。
    requestsResponse = envelope({
      items: [
        {
          _id: 'req-1',
          user_oid: 'u1',
          period: '202610',
          kind: 'ocr',
          status: 'pending',
          reason: '活动报名要用',
          requested_limit: 800,
          decided_by: null,
          decided_at: null,
          decision_note: null,
          created_at: '2026-10-28 10:00:00',
          updated_at: '2026-10-28 10:00:00',
          user_name: '张三',
          user_id: '2024001',
          business_id: '2024001'
        }
      ],
      total: 1,
      page: 1,
      size: 20
    })
    const wrapper = await mountPage()

    const text = wrapper.text()
    expect(text).toContain('ai.quotaAdmin.requests.title')
    expect(text).toContain('张三')
    expect(text).toContain('活动报名要用')
    expect(text).toContain('800')
    expect(text).toContain('ai.quotaAdmin.ocrTitle')

    wrapper.unmount()
  })

  it('批准：默认只发 {reset:true}，选择设新上限时带 limit', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.quotaAdmin.requests.approve')!.trigger('click')
    await flushPromises()

    let dialog = wrapper.find('.el-dialog-stub')
    expect(dialog.exists()).toBe(true)
    await dialogButton(dialog, 'ai.quotaAdmin.requests.approve')!.trigger('click')
    await flushPromises()

    const firstApprove = axiosMock.mock.calls.find(([call]) => call.url === '/ai/quota/requests/req-1/approve')![0]
    expect(firstApprove.data).toEqual({ reset: true })

    await buttonByText(wrapper, 'ai.quotaAdmin.requests.approve')!.trigger('click')
    await flushPromises()
    dialog = wrapper.find('.el-dialog-stub')
    await dialog.find('.el-radio-group-stub').setValue('reset_limit')
    await flushPromises()
    await dialogButton(dialog, 'ai.quotaAdmin.requests.approve')!.trigger('click')
    await flushPromises()

    const approveCalls = axiosMock.mock.calls.filter(
      ([call]) => call.url === '/ai/quota/requests/req-1/approve'
    )
    expect(approveCalls.length).toBe(2)
    expect(approveCalls[1]![0].data).toEqual({ reset: true, limit: 800 })

    wrapper.unmount()
  })

  it('拒绝：带上填写的理由', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.quotaAdmin.requests.reject')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.find('.el-input-stub').setValue('额度已经够用了')
    await dialogButton(dialog, 'ai.quotaAdmin.requests.reject')!.trigger('click')
    await flushPromises()

    const rejectCall = axiosMock.mock.calls.find(([call]) => call.url === '/ai/quota/requests/req-1/reject')![0]
    expect(rejectCall.data).toEqual({ note: '额度已经够用了' })

    wrapper.unmount()
  })

  it('空态：用户与申请都为空时有明确提示；无权限时整页只读', async () => {
    usersResponse = envelope(page<AiQuotaUserRow>([]))
    requestsResponse = envelope(page<AiQuotaRequestAdminItem>([]))
    const wrapper = await mountPage()

    const text = wrapper.text()
    expect(text).toContain('ai.quotaAdmin.users.empty')
    expect(text).toContain('ai.quotaAdmin.requests.empty')
    wrapper.unmount()

    const usersCallsBefore = axiosMock.mock.calls.filter(
      ([call]) => call.url === '/ai/quota/users'
    ).length
    storeState.position = ['student']
    const denied = await mountPage()
    expect(denied.text()).toContain('ai.quotaAdmin.noPermission')
    expect(denied.text()).not.toContain('ai.quotaAdmin.users.title')
    expect(
      axiosMock.mock.calls.filter(([call]) => call.url === '/ai/quota/users').length
    ).toBe(usersCallsBefore)
    denied.unmount()
  })

  it('加载失败时展示后端 detail', async () => {
    usersResponse = failure(403, { code: 'AI_FORBIDDEN', message: '后端说明：没有权限' })
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('后端说明：没有权限')
    expect(wrapper.text()).toContain('ai.quotaAdmin.retry')

    wrapper.unmount()
  })
})
