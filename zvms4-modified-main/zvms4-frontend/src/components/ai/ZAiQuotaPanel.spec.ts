import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

function snapshot(ocrUsed: number, ocrLimit: number, chatUsed = 0, chatLimit = 1000000) {
  const view: AiQuotaSnapshotView = {
    ocr: { period: '202610', used: ocrUsed, limit: ocrLimit, source: 'explicit' },
    chat: { period: '202610', used: chatUsed, limit: chatLimit, source: 'role_default' }
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

let quota: AiQuotaSnapshotView
let mine: unknown[]
let createResult: () => Promise<unknown>

beforeEach(() => {
  axiosMock.mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()

  quota = snapshot(7, 500)
  mine = []
  createResult = async () => {
    throw new AiRequestError({ status: 500, code: 'AI_HTTP_500', message: 'unexpected' })
  }

  axiosMock.mockImplementation(async (config: AxiosCall) => {
    if (config.method === 'get' && config.url === '/ai/quota') {
      return envelope(quota)
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
    expect(buttonByText(wrapper, 'ai.quota.request.apply')).toBeUndefined()

    wrapper.unmount()
  })

  it('某个能力用尽时，只在该行出现「申请重置额度」', async () => {
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
    createResult = async () => envelope(pendingRequest('ocr'))
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
        data: { kind: 'ocr', reason: '活动报名要用', requested_limit: 800 }
      })
    )
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.quota.request.submitted')

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
})
