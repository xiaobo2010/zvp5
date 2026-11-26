import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VNodeChild } from 'vue'
import ZAiModelsAdmin from './ZAiModelsAdmin.vue'
import { AiRequestError } from '@/api/ai/errors'
import type { AiModelDiscoverRow, AiModelRow } from '@/../types/ai'

/**
 * 模型管理页。
 *
 * 请求层不 mock：`@/plugins/axios` 才是替身，models API 按真实路径跑一遍，
 * envelope 拆包与 AiRequestError 映射都在链路里。
 */

interface AxiosCall {
  method: string
  url: string
  data?: Record<string, unknown>
  params?: Record<string, unknown>
}

const { axiosMock, elMessageErrorSpy, elMessageSuccessSpy, elMessageBoxConfirmSpy } = vi.hoisted(
  () => ({
    axiosMock: vi.fn<(config: AxiosCall) => Promise<unknown>>(),
    elMessageErrorSpy: vi.fn(),
    elMessageSuccessSpy: vi.fn(),
    elMessageBoxConfirmSpy: vi.fn()
  })
)

vi.mock('@/plugins/axios', () => ({ default: axiosMock, baseURL: '' }))

// 裸 reauth 会走 short-token → ElMessageBox.prompt（本文件只 mock 了 confirm），
// 这里直接给一张固定令牌，断言也只关心它被带进请求体。
vi.mock('@/plugins/reauth', () => ({
  withReauth: (_reason: string, action: (token: string) => Promise<unknown>) =>
    action('verify-token'),
  isReauthCancelled: () => false
}))

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

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElButton = defineComponent({
    name: 'ElButton',
    props: {
      type: String,
      size: String,
      loading: Boolean,
      disabled: Boolean,
      text: Boolean,
      title: String
    },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            title: props.title,
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

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: {
      modelValue: { type: String, default: '' },
      placeholder: String,
      disabled: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'select',
          {
            class: 'el-select-stub',
            value: props.modelValue,
            disabled: props.disabled || undefined,
            onChange: (event: Event) =>
              emit('update:modelValue', (event.target as HTMLSelectElement).value)
          },
          slots.default?.()
        )
    }
  })

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: String },
    setup(props) {
      return () => h('option', { value: props.value ?? '' }, props.label ?? '')
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
    props: { modelValue: { type: Number, default: null }, min: Number, step: Number },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-number-stub',
          value: props.modelValue ?? '',
          onInput: (event: Event) =>
            emit('update:modelValue', Number((event.target as HTMLInputElement).value))
        })
    }
  })

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: { modelValue: Boolean, disabled: Boolean },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-switch-stub',
          type: 'checkbox',
          checked: props.modelValue,
          onChange: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).checked)
        })
    }
  })

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String, effect: String },
    setup(props, { slots }) {
      return () =>
        h('span', { class: 'el-tag-stub', 'data-type': props.type ?? '' }, slots.default?.())
    }
  })

  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
    }
  })

  // ElTable / ElTableColumn 桩：保留「表头 + 每行单元格」，单元格仍走各列的作用域插槽。
  const ElTable = defineComponent({
    name: 'ElTable',
    props: { data: { type: Array, default: () => [] } },
    setup(props, { slots }) {
      return () => {
        const columns = slots.default?.() ?? []
        const labelOf = (column: (typeof columns)[number]) =>
          String((column.props as { label?: string } | null)?.label ?? '')
        const cellOf = (column: (typeof columns)[number], row: unknown) => {
          const cellSlots = column.children as
            | { default?: (scope: { row: unknown }) => VNodeChild }
            | null
          return cellSlots?.default?.({ row }) ?? []
        }
        return h('table', { class: 'el-table-stub' }, [
          h(
            'thead',
            h(
              'tr',
              columns.map((column, index) => h('th', { key: index }, labelOf(column)))
            )
          ),
          h(
            'tbody',
            (props.data as unknown[]).map((row, rowIndex) =>
              h(
                'tr',
                { key: rowIndex },
                columns.map((column, index) => h('td', { key: index }, cellOf(column, row)))
              )
            )
          )
        ])
      }
    }
  })

  const ElTableColumn = defineComponent({
    name: 'ElTableColumn',
    props: { prop: String, label: String, minWidth: [String, Number], width: [String, Number] },
    setup() {
      return () => null
    }
  })

  return {
    ElButton,
    ElDialog,
    ElForm,
    ElFormItem,
    ElInput,
    ElInputNumber,
    ElSelect,
    ElOption,
    ElSwitch,
    ElTag,
    ElIcon,
    ElTable,
    ElTableColumn,
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

const catalogRow: AiModelRow = {
  model: 'gpt-4o',
  display_name: 'GPT-4o',
  provider: 'openai',
  providers: ['openai', 'azure'],
  context_length: 128000,
  rate: 0.5,
  enabled: true,
  in_catalog: true,
  notes: null,
  updated_at: '2026-10-28 10:00:00'
}

const manualRow: AiModelRow = {
  model: 'local-llm',
  display_name: null,
  provider: 'local',
  providers: [],
  context_length: null,
  rate: null,
  enabled: false,
  in_catalog: false,
  notes: '只在本机跑',
  updated_at: null
}

function envelope<T>(data: T): unknown {
  return { status: 200, data: { status: 'ok', data }, headers: {} }
}

let models: AiModelRow[]
let modelsResult: () => Promise<unknown>

/** discover 上游返回：预览与回写共用一个桩，`applied` 只在 apply=true 时生效。 */
let discoverRows: AiModelDiscoverRow[]
let discoverApplied: number
let discoverFailure: unknown

beforeEach(() => {
  axiosMock.mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')

  models = [catalogRow, manualRow]
  modelsResult = async () => envelope({ models })
  discoverRows = [
    { model: 'gpt-4o', context_length: 128000, in_credential: true, applied: false },
    { model: 'o3-preview', context_length: null, in_credential: false, applied: false }
  ]
  discoverApplied = 0
  discoverFailure = null

  axiosMock.mockImplementation(async (config: AxiosCall) => {
    if (config.method === 'get' && config.url === '/ai/models') {
      return modelsResult()
    }
    if (config.method === 'post' && config.url === '/ai/models/discover') {
      if (discoverFailure) {
        throw discoverFailure
      }
      const body = config.data as { provider: string; apply?: boolean }
      return envelope({
        provider: body.provider,
        models: discoverRows.map((row) => ({
          ...row,
          applied: body.apply === true && row.applied
        })),
        applied: body.apply === true ? discoverApplied : 0
      })
    }
    if (config.method === 'put' && config.url.startsWith('/ai/models/')) {
      const model = decodeURIComponent(config.url.slice('/ai/models/'.length))
      const updated = { ...models.find((row) => row.model === model)!, ...config.data }
      models = models.map((row) => (row.model === model ? (updated as AiModelRow) : row))
      return envelope(updated)
    }
    if (config.method === 'delete' && config.url.startsWith('/ai/models/')) {
      return { status: 204, data: null, headers: {} }
    }
    throw new Error(`unexpected request ${config.method} ${config.url}`)
  })
})

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(ZAiModelsAdmin)
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((item) => item.text() === text)
}

function called(config: Partial<AxiosCall>): boolean {
  return axiosMock.mock.calls.some(([call]) =>
    Object.entries(config).every(([key, value]) => call[key as keyof AxiosCall] === value)
  )
}

describe('模型管理页', () => {
  it('渲染模型、渠道、显示名、上下文窗口、倍率与来源标记', async () => {
    const wrapper = await mountPage()

    const text = wrapper.text()
    expect(text).toContain('gpt-4o')
    expect(text).toContain('openai')
    expect(text).toContain('azure')
    expect(text).toContain('GPT-4o')
    expect(text).toContain('128000')
    expect(text).toContain('0.5')
    expect(text).toContain('ai.models.inCatalog')
    expect(text).toContain('ai.models.manual')

    // 手工登记行没有 providers，回落到 provider
    expect(text).toContain('local')
    expect(text).toContain('ai.common.disabled')

    wrapper.unmount()
  })

  it('手工登记的行不能删除', async () => {
    const wrapper = await mountPage()

    const deleteButtons = wrapper
      .findAll('button')
      .filter((item) => item.text() === 'ai.models.delete')
    expect(deleteButtons.length).toBe(2)
    expect(deleteButtons[0]!.attributes('disabled')).toBeUndefined()
    expect(deleteButtons[1]!.attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('编辑保存只发改动字段，并刷新列表', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.models.edit')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog-stub')
    expect(dialog.exists()).toBe(true)

    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('GPT 4o')
    const numbers = dialog.findAll('.el-input-number-stub')
    await numbers[1]!.setValue('1.25')

    await buttonByText(wrapper, 'ai.common.save')!.trigger('click')
    await flushPromises()

    const putCall = axiosMock.mock.calls.find(([call]) => call.method === 'put')![0]
    expect(putCall.url).toBe('/ai/models/gpt-4o')
    expect(putCall.data).toEqual({ display_name: 'GPT 4o', rate: 1.25 })
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.models.saveOk')

    const gets = axiosMock.mock.calls.filter(
      ([call]) => call.method === 'get' && call.url === '/ai/models'
    )
    expect(gets.length).toBe(2)

    wrapper.unmount()
  })

  it('倍率非法时拦截，不发 PUT', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.models.edit')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-number-stub')[1]!.setValue('0')
    await buttonByText(wrapper, 'ai.common.save')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.models.invalidRate')
    expect(called({ method: 'put' })).toBe(false)
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('上下文窗口非正整数时拦截', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.models.edit')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-number-stub')[0]!.setValue('0.5')
    await buttonByText(wrapper, 'ai.common.save')!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai.models.invalidContext')
    expect(called({ method: 'put' })).toBe(false)

    wrapper.unmount()
  })

  it('删除覆盖前二次确认，确认后走 DELETE 并移除该行', async () => {
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.models.delete')!.trigger('click')
    await flushPromises()

    expect(elMessageBoxConfirmSpy).toHaveBeenCalled()
    expect(called({ method: 'delete', url: '/ai/models/gpt-4o' })).toBe(true)
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.models.deletedOk')
    expect(wrapper.text()).not.toContain('gpt-4o')

    wrapper.unmount()
  })

  it('确认框取消时不发 DELETE', async () => {
    elMessageBoxConfirmSpy.mockRejectedValue(new Error('cancel'))
    const wrapper = await mountPage()

    await buttonByText(wrapper, 'ai.models.delete')!.trigger('click')
    await flushPromises()

    expect(called({ method: 'delete' })).toBe(false)
    expect(wrapper.text()).toContain('gpt-4o')

    wrapper.unmount()
  })

  it('没有模型时显示空态', async () => {
    models = []
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('ai.models.empty')
    expect(wrapper.find('.el-table-stub').exists()).toBe(false)

    wrapper.unmount()
  })

  it('加载失败展示后端 detail 与重试按钮', async () => {
    modelsResult = async () => {
      throw new AiRequestError({
        status: 502,
        code: 'AI_UPSTREAM_ERROR',
        message: '模型列表暂不可用'
      })
    }
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('模型列表暂不可用')
    expect(buttonByText(wrapper, 'ai.models.retry')).toBeDefined()
    expect(wrapper.find('.el-table-stub').exists()).toBe(false)

    wrapper.unmount()
  })
})

async function openDiscoverDialog(wrapper: VueWrapper) {
  await buttonByText(wrapper, 'ai.models.discover.open')!.trigger('click')
  await flushPromises()
}

async function fetchPreview(wrapper: VueWrapper) {
  await buttonByText(wrapper, 'ai.models.discover.fetch')!.trigger('click')
  await flushPromises()
}

function discoverApplyButton(wrapper: VueWrapper) {
  return buttonByText(wrapper, 'ai.models.discover.apply')!
}

function discoverCalls() {
  return axiosMock.mock.calls
    .filter(([call]) => call.url === '/ai/models/discover')
    .map(([call]) => call.data as Record<string, unknown>)
}

describe('从上游获取模型元数据', () => {
  it('打开弹窗，渠道选项是当前行 providers 的并集', async () => {
    const wrapper = await mountPage()

    await openDiscoverDialog(wrapper)

    expect(wrapper.find('.el-dialog-stub').attributes('data-title')).toBe(
      'ai.models.discover.title'
    )
    const options = wrapper.findAll('.el-select-stub option').map((item) => item.text())
    // openai / azure 来自目录行的 providers，local 来自手工行的 provider，且不重复
    expect(options).toEqual(['openai', 'azure', 'local'])
    expect(wrapper.text()).toContain('ai.models.discover.idle')

    wrapper.unmount()
  })

  it('预览渲染上游上下文长度，「未提供」与状态标签', async () => {
    const wrapper = await mountPage()

    await openDiscoverDialog(wrapper)
    await wrapper.find('.el-select-stub').setValue('azure')
    await fetchPreview(wrapper)

    const dialogText = wrapper.find('.el-dialog-stub').text()
    expect(dialogText).toContain('gpt-4o')
    expect(dialogText).toContain('128000')
    expect(dialogText).toContain('o3-preview')
    expect(dialogText).toContain('ai.models.discover.contextMissing')
    expect(dialogText).toContain('ai.models.discover.statusPending')
    expect(dialogText).toContain('ai.models.discover.yes')
    expect(dialogText).toContain('ai.models.discover.no')

    expect(discoverCalls()).toEqual([
      { provider: 'azure', apply: false, verification_token: 'verify-token' }
    ])

    wrapper.unmount()
  })

  it('上游没有返回模型时显示空态，且回写按钮禁用', async () => {
    discoverRows = []
    const wrapper = await mountPage()

    await openDiscoverDialog(wrapper)
    await fetchPreview(wrapper)

    expect(wrapper.find('.el-dialog-stub').text()).toContain('ai.models.discover.empty')
    expect(discoverApplyButton(wrapper).attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('获取失败展示后端 detail，并可重试', async () => {
    discoverFailure = new AiRequestError({
      status: 404,
      code: 'AI_HTTP_404',
      message: '渠道不存在'
    })
    const wrapper = await mountPage()

    await openDiscoverDialog(wrapper)
    await fetchPreview(wrapper)

    const dialogText = wrapper.find('.el-dialog-stub').text()
    expect(dialogText).toContain('渠道不存在')
    expect(buttonByText(wrapper, 'ai.models.discover.retry')).toBeDefined()
    expect(discoverApplyButton(wrapper).attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('回写成功后提示 applied 数量并重新拉取列表', async () => {
    discoverRows = [
      { model: 'gpt-4o', context_length: 128000, in_credential: true, applied: true },
      { model: 'o3-preview', context_length: 64000, in_credential: false, applied: true }
    ]
    discoverApplied = 2
    const wrapper = await mountPage()

    await openDiscoverDialog(wrapper)
    await fetchPreview(wrapper)
    await discoverApplyButton(wrapper).trigger('click')
    await flushPromises()

    expect(elMessageSuccessSpy).toHaveBeenCalledWith(
      'ai.models.discover.applyOk {"count":2}'
    )
    expect(wrapper.find('.el-dialog-stub').text()).toContain('ai.models.discover.statusApplied')

    const calls = discoverCalls()
    expect(calls[1]).toEqual({ provider: 'openai', apply: true, verification_token: 'verify-token' })

    const gets = axiosMock.mock.calls.filter(
      ([call]) => call.method === 'get' && call.url === '/ai/models'
    )
    expect(gets.length).toBe(2)

    wrapper.unmount()
  })

  it('没有预览时回写按钮不可用', async () => {
    const wrapper = await mountPage()

    await openDiscoverDialog(wrapper)
    expect(discoverApplyButton(wrapper).attributes('disabled')).toBeDefined()

    await fetchPreview(wrapper)
    expect(discoverApplyButton(wrapper).attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })
})
