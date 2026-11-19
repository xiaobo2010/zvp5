import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VNodeChild } from 'vue'
import ZAiQuotaPanel from './ZAiQuotaPanel.vue'
import { AiRequestError } from '@/api/ai/errors'
import type { AiQuotaRequest, AiQuotaSnapshotView } from '@/api/ai'

/**
 * 我的额度面板。
 *
 * 请求层不 mock：`@/plugins/axios` 才是替身，quota API 文件按真实路径跑一遍，
 * envelope 拆包与 AiRequestError 的 status/detail 映射都在链路里。
 */

interface AxiosCall {
  method: string
  url: string
  data?: Record<string, unknown>
  params?: Record<string, unknown>
}

const { axiosMock, elMessageErrorSpy, elMessageSuccessSpy } = vi.hoisted(() => ({
  axiosMock: vi.fn<(config: AxiosCall) => Promise<unknown>>(),
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn()
}))

vi.mock('@/plugins/axios', () => ({ default: axiosMock, baseURL: '' }))

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
          ? h('div', { class: 'el-dialog-stub' }, [slots.default?.(), slots.footer?.()])
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

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String, effect: String },
    setup(props, { slots }) {
      return () =>
        h(
          'span',
          { class: 'el-tag-stub', 'data-type': props.type ?? '' },
          slots.default?.()
        )
    }
  })

  const ElProgress = defineComponent({
    name: 'ElProgress',
    props: { percentage: Number, strokeWidth: Number },
    setup() {
      return () => h('div', { class: 'el-progress-stub' })
    }
  })

  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
    }
  })

  // ElTable / ElTableColumn 桩：真实表格会连带引入 theme-chalk 的 css。
  // 这里保留「表头 + 每行单元格」的最小结构，单元格仍走各列的作用域插槽。
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
          h('thead', [
            h(
              'tr',
              columns.map((column, index) => h('th', { key: index }, labelOf(column)))
            )
          ]),
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
    props: { label: String, minWidth: [String, Number], align: String },
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
    ElTag,
    ElProgress,
    ElIcon,
    ElTable,
    ElTableColumn,
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    })
  }
})

function envelope<T>(data: T): unknown {
  return { status: 200, data: { status: 'ok', data }, headers: {} }
}

/** 唯一额度池：对话与 OCR 都记在它上面。 */
function snapshot(used: number, limit: number) {
  const view: AiQuotaSnapshotView = {
    af: { period: '202610', used, limit, source: 'explicit' }
  }
  return view
}

function pendingRequest(kind: 'chat' | 'ocr'): AiQuotaRequest {
  return {
    oid: 'req-1',
    period: '202610',
    kind,
    status: 'pending',
    reason: '活动报名要用',
    requested_limit: 800,
    created_at: '2026-10-28 10:00:00',
    decided_at: null,
    decision_note: null
  }
}

/** 当月用量统计的样例：两个渠道（其中一个未标注）+ 一个模型。 */
function usageFixture() {
  return {
    period: '202610',
    total: {
      calls: 12,
      units: 12,
      prompt_tokens: 900,
      completion_tokens: 300,
      total_tokens: 1200
    },
    by_provider: [
      {
        provider: 'openai-main',
        calls: 5,
        units: 5,
        prompt_tokens: 900,
        completion_tokens: 300,
        total_tokens: 1200
      },
      {
        provider: null,
        calls: 7,
        units: 7,
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0
      }
    ],
    by_model: [
      {
        provider: 'openai-main',
        model: 'gpt-4o-mini',
        calls: 5,
        units: 5,
        prompt_tokens: 900,
        completion_tokens: 300,
        total_tokens: 1200
      },
      {
        provider: 'openai-main',
        model: null,
        calls: 2,
        units: 2,
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0
      }
    ]
  }
}

let quota: AiQuotaSnapshotView
let mine: unknown[]
let createResult: () => Promise<unknown>
let usage: unknown
let usageResult: () => Promise<unknown>

beforeEach(() => {
  axiosMock.mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()

  quota = snapshot(7, 500)
  mine = []
  usage = usageFixture()
  usageResult = async () => envelope(usage)
  createResult = async () => {
    throw new AiRequestError({ status: 500, code: 'AI_HTTP_500', message: 'unexpected' })
  }

  axiosMock.mockImplementation(async (config: AxiosCall) => {
    if (config.method === 'get' && config.url === '/ai/quota') {
      return envelope(quota)
    }
    if (config.method === 'get' && config.url === '/ai/quota/usage') {
      return usageResult()
    }
    if (config.method === 'get' && config.url === '/ai/quota/requests/mine') {
      return envelope({ items: mine })
    }
    if (config.method === 'post' && config.url === '/ai/quota/requests') {
      return createResult()
    }
    throw new Error(`unexpected request ${config.method} ${config.url}`)
  })
})

async function mountPanel(): Promise<VueWrapper> {
  const wrapper = mount(ZAiQuotaPanel)
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((item) => item.text() === text)
}

describe('我的额度面板', () => {
  it('展示用量与来源；未用尽时不出现申请按钮', async () => {
    const wrapper = await mountPanel()

    const text = wrapper.text()
    expect(text).toContain('7')
    expect(text).toContain('500')
    expect(text).toContain('ai.quotaAdmin.sourceExplicit')
    expect(text).toContain('ai.quota.afTitle')
    expect(buttonByText(wrapper, 'ai.quota.request.apply')).toBeUndefined()

    wrapper.unmount()
  })

  it('AF 池用尽时出现「申请重置额度」', async () => {
    quota = snapshot(500, 500)
    const wrapper = await mountPanel()

    const applyButtons = wrapper
      .findAll('button')
      .filter((item) => item.text() === 'ai.quota.request.apply')
    expect(applyButtons.length).toBe(1)

    wrapper.unmount()
  })

  it('limit 为 0（不限量）不算用尽', async () => {
    quota = snapshot(100, 0)
    const wrapper = await mountPanel()

    expect(buttonByText(wrapper, 'ai.quota.request.apply')).toBeUndefined()

    wrapper.unmount()
  })

  it('提交申请时发出 kind / reason / requested_limit', async () => {
    quota = snapshot(500, 500)
    createResult = async () => envelope(pendingRequest('chat'))
    const wrapper = await mountPanel()

    await buttonByText(wrapper, 'ai.quota.request.apply')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    await wrapper.find('.el-input-stub').setValue('活动报名要用')
    await wrapper.find('.el-input-number-stub').setValue('800')
    await buttonByText(wrapper, 'ai.quota.request.submit')!.trigger('click')
    await flushPromises()

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: '/ai/quota/requests',
        data: { kind: 'chat', reason: '活动报名要用', requested_limit: 800 }
      })
    )
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.quota.request.submitted')

    wrapper.unmount()
  })

  it('消耗类型可切换：换成 OCR 后按 ocr 提交（额度仍落在同一个 AF 池）', async () => {
    quota = snapshot(500, 500)
    createResult = async () => envelope(pendingRequest('ocr'))
    const wrapper = await mountPanel()

    await buttonByText(wrapper, 'ai.quota.request.apply')!.trigger('click')
    await flushPromises()
    await wrapper.find('.el-select-stub').setValue('ocr')
    await flushPromises()
    await buttonByText(wrapper, 'ai.quota.request.submit')!.trigger('click')
    await flushPromises()

    expect(axiosMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: '/ai/quota/requests',
        data: { kind: 'ocr' }
      })
    )

    wrapper.unmount()
  })

  it('提交成功后按钮变「已申请」并出现待审批条目', async () => {
    quota = snapshot(500, 500)
    createResult = async () => envelope(pendingRequest('ocr'))
    const wrapper = await mountPanel()

    await buttonByText(wrapper, 'ai.quota.request.apply')!.trigger('click')
    await flushPromises()
    await buttonByText(wrapper, 'ai.quota.request.submit')!.trigger('click')
    await flushPromises()

    const applied = buttonByText(wrapper, 'ai.quota.request.applied')
    expect(applied).toBeDefined()
    expect(applied!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('ai.quota.request.pendingTitle')

    wrapper.unmount()
  })

  it('本人已有待审批申请时，进入页面就是禁用态', async () => {
    quota = snapshot(500, 500)
    // 后端实际下发的是仓储行（`_id`）且直接是数组，不是 {items} 包装。
    mine = [
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
        updated_at: '2026-10-28 10:00:00'
      }
    ]
    const wrapper = await mountPanel()

    const applied = buttonByText(wrapper, 'ai.quota.request.applied')
    expect(applied).toBeDefined()
    expect(applied!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('活动报名要用')

    wrapper.unmount()
  })

  it('409（已有 pending）静默切态，不弹错误', async () => {
    quota = snapshot(500, 500)
    createResult = async () => {
      throw new AiRequestError({ status: 409, code: 'AI_INVALID_REQUEST' })
    }
    const wrapper = await mountPanel()

    await buttonByText(wrapper, 'ai.quota.request.apply')!.trigger('click')
    await flushPromises()
    await buttonByText(wrapper, 'ai.quota.request.submit')!.trigger('click')
    await flushPromises()

    expect(elMessageErrorSpy).not.toHaveBeenCalled()
    const applied = buttonByText(wrapper, 'ai.quota.request.applied')
    expect(applied).toBeDefined()
    expect(applied!.attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('其它错误展示后端 detail', async () => {
    quota = snapshot(500, 500)
    createResult = async () => {
      throw new AiRequestError({
        status: 400,
        code: 'AI_INVALID_REQUEST',
        message: '申请说明过长'
      })
    }
    const wrapper = await mountPanel()

    await buttonByText(wrapper, 'ai.quota.request.apply')!.trigger('click')
    await flushPromises()
    await buttonByText(wrapper, 'ai.quota.request.submit')!.trigger('click')
    await flushPromises()

    expect(elMessageErrorSpy).toHaveBeenCalledWith('申请说明过长')
    expect(buttonByText(wrapper, 'ai.quota.request.apply')).toBeDefined()

    wrapper.unmount()
  })

  describe('本月用量', () => {
    function usageCard(wrapper: VueWrapper) {
      return wrapper
        .findAll('section')
        .find((item) => item.text().includes('ai.quota.usage.title'))
    }

    it('渲染汇总数字与按渠道、按模型两张表', async () => {
      const wrapper = await mountPanel()
      const card = usageCard(wrapper)!

      expect(card).toBeDefined()
      expect(card.text()).toContain('202610')
      expect(card.text()).toContain('ai.quota.usage.calls')
      expect(card.text()).toContain('1200')
      expect(card.text()).toContain('ai.quota.usage.units')

      const tables = card.findAll('.el-table-stub')
      expect(tables.length).toBe(2)

      const providerTable = tables[0]
      expect(providerTable.findAll('thead th').map((th) => th.text())).toEqual([
        'ai.quota.usage.provider',
        'ai.quota.usage.calls',
        'ai.quota.usage.tokens',
        'ai.quota.usage.units'
      ])
      expect(providerTable.findAll('tbody tr').length).toBe(2)
      expect(providerTable.text()).toContain('openai-main')
      // provider 为 null 时显示占位文案，不是空白或 "null"
      expect(providerTable.text()).toContain('ai.quota.usage.unlabeled')

      const modelTable = tables[1]
      expect(modelTable.findAll('thead th').map((th) => th.text())).toEqual([
        'ai.quota.usage.provider',
        'ai.quota.usage.model',
        'ai.quota.usage.calls',
        'ai.quota.usage.tokens',
        'ai.quota.usage.units'
      ])
      expect(modelTable.findAll('tbody tr').length).toBe(2)
      expect(modelTable.text()).toContain('gpt-4o-mini')
      expect(modelTable.text()).toContain('ai.quota.usage.unlabeled')

      // period 省略时请求不带查询参数
      expect(axiosMock).toHaveBeenCalledWith(
        expect.objectContaining({ method: 'get', url: '/ai/quota/usage', params: {} })
      )

      wrapper.unmount()
    })

    it('没有任何记录时显示空态', async () => {
      usage = {
        period: '202610',
        total: {
          calls: 0,
          units: 0,
          prompt_tokens: 0,
          completion_tokens: 0,
          total_tokens: 0
        },
        by_provider: [],
        by_model: []
      }
      const wrapper = await mountPanel()
      const card = usageCard(wrapper)!

      expect(card.text()).toContain('ai.quota.usage.empty')
      expect(card.findAll('.el-table-stub').length).toBe(0)
      expect(card.findAll('button').some((item) => item.text() === 'ai.quota.usage.retry')).toBe(
        false
      )

      wrapper.unmount()
    })

    it('加载失败只降级用量卡，不阻塞上方额度卡', async () => {
      usageResult = async () => {
        throw new AiRequestError({
          status: 502,
          code: 'AI_UPSTREAM_ERROR',
          message: '用量统计暂不可用'
        })
      }
      const wrapper = await mountPanel()

      const card = usageCard(wrapper)!
      expect(card.text()).toContain('用量统计暂不可用')
      expect(card.findAll('.el-table-stub').length).toBe(0)
      expect(card.findAll('button').some((item) => item.text() === 'ai.quota.usage.retry')).toBe(
        true
      )

      // 额度卡照常显示：失败被限制在用量卡内部
      const quotaCard = wrapper
        .findAll('section')
        .find((item) => item.text().includes('ai.quota.title'))!
      expect(quotaCard.text()).toContain('500')

      wrapper.unmount()
    })
  })
})
