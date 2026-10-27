import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiQuotaAdmin from './ZAiQuotaAdmin.vue'
import { resetUserQuota, setUserLimit, userQuota } from '@/api/ai'
import type { AiQuotaSnapshotView } from '@/api/ai'

const { elMessageErrorSpy, elMessageSuccessSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    userQuota: vi.fn(),
    setUserLimit: vi.fn(),
    resetUserQuota: vi.fn()
  }
})

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

vi.mock('@/components/form/ZSelectPerson.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'ZSelectPerson',
      props: {
        modelValue: { type: [String, Array], default: '' },
        filterStart: Number,
        fullWidth: Boolean
      },
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
    props: { modelValue: Boolean, title: String, width: String },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub' }, [
              h('header', props.title),
              slots.default?.(),
              slots.footer?.()
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

  const ElInputNumber = defineComponent({
    name: 'ElInputNumber',
    props: { modelValue: { default: 0 }, min: Number, step: Number },
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
    setup() {
      return () => h('div', { class: 'el-select-stub' })
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
          {
            class: 'el-tag-stub',
            'data-type': props.type ?? '',
            'data-effect': props.effect ?? ''
          },
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
    }),
    ElMessageBox: { confirm: vi.fn() }
  }
})

function snapshot(
  ocrSource: AiQuotaSnapshotView['ocr']['source'],
  chatSource: AiQuotaSnapshotView['chat']['source']
): AiQuotaSnapshotView {
  return {
    ocr: { period: '202610', used: 7, limit: 500, source: ocrSource },
    chat: { period: '202610', used: 0, limit: 1000000, source: chatSource }
  }
}

async function mountWithUser(): Promise<VueWrapper> {
  const wrapper = mount(ZAiQuotaAdmin)
  await wrapper.find('.z-select-person-stub').setValue('user-1')
  await flushPromises()
  return wrapper
}

function tags(wrapper: VueWrapper) {
  return wrapper.findAll('.el-tag-stub').map((item) => item.text())
}

beforeEach(() => {
  vi.mocked(userQuota).mockReset()
  vi.mocked(setUserLimit).mockReset()
  vi.mocked(resetUserQuota).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  vi.mocked(userQuota).mockResolvedValue(snapshot('explicit', 'role_default'))
})

describe('额度管理页', () => {
  it('选人后按 oid 拉取额度', async () => {
    const wrapper = await mountWithUser()

    expect(userQuota).toHaveBeenCalledWith('user-1')

    wrapper.unmount()
  })

  it('explicit 显示「库内已固化」、role_default 显示「按角色默认」', async () => {
    const wrapper = await mountWithUser()

    expect(tags(wrapper)).toEqual([
      'ai.quotaAdmin.sourceExplicit',
      'ai.quotaAdmin.sourceRoleDefault'
    ])

    wrapper.unmount()
  })

  it('global_default 显示「全局默认」', async () => {
    vi.mocked(userQuota).mockResolvedValue(snapshot('global_default', 'global_default'))
    const wrapper = await mountWithUser()

    expect(tags(wrapper)).toEqual([
      'ai.quotaAdmin.sourceGlobalDefault',
      'ai.quotaAdmin.sourceGlobalDefault'
    ])

    wrapper.unmount()
  })

  it('来源标签用次要色（info / plain），不用警告色', async () => {
    const wrapper = await mountWithUser()

    for (const tag of wrapper.findAll('.el-tag-stub')) {
      expect(tag.attributes('data-type')).toBe('info')
      expect(tag.attributes('data-effect')).toBe('plain')
    }

    wrapper.unmount()
  })

  it('自然月口径只说明一次，放在列表上方', async () => {
    const wrapper = await mountWithUser()

    const hints = wrapper
      .findAll('p')
      .filter((item) => item.text() === 'ai.quotaAdmin.monthlyNotice')
    expect(hints.length).toBe(1)

    wrapper.unmount()
  })

  it('后端没给 source 时不渲染标签', async () => {
    vi.mocked(userQuota).mockResolvedValue({
      ocr: { period: '202610', used: 1, limit: 10 },
      chat: { period: '202610', used: 2, limit: 20 }
    })
    const wrapper = await mountWithUser()

    expect(tags(wrapper)).toEqual([])

    wrapper.unmount()
  })

  it('选人→加载快照→设置额度的交互闭环', async () => {
    vi.mocked(setUserLimit).mockResolvedValue({
      kind: 'ocr',
      period: '202610',
      limit: 300,
      used: 7
    })
    const wrapper = await mountWithUser()

    const setLimitButton = wrapper
      .findAll('button')
      .find((item) => item.text() === 'ai.quotaAdmin.setLimit')
    expect(setLimitButton).toBeDefined()
    await setLimitButton!.trigger('click')
    await flushPromises()

    await wrapper.find('.el-input-number-stub').setValue('300')
    const saveButton = wrapper
      .findAll('button')
      .find((item) => item.text() === 'ai.common.save')
    expect(saveButton).toBeDefined()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(setUserLimit).toHaveBeenCalledWith('user-1', 'ocr', 300)
    expect(userQuota).toHaveBeenCalledTimes(2)
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.quotaAdmin.savedNotice')

    wrapper.unmount()
  })

  it('额度仍按 used / limit 展示', async () => {
    const wrapper = await mountWithUser()

    const text = wrapper.text()
    expect(text).toContain('7')
    expect(text).toContain('500')
    expect(text).toContain('1000000')

    wrapper.unmount()
  })
})
