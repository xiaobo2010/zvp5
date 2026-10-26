import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZSystemSettings from './ZSystemSettings.vue'
import {
  listSettings,
  settingErrorMessage,
  settingErrorStatus,
  updateSettings
} from '@/api/system/settings'
import type { SystemSettingsData } from '@/api/system/settings'

/**
 * 设置页的渲染与保存行为。
 *
 * 纯逻辑在 settingsLogic.spec.ts；这里盯住「界面上的几件要紧事」：
 * 分组渲染、待保存标记、只提交改动项、403 给出明确说明而不是空白页。
 */

const { elMessageErrorSpy, elMessageSuccessSpy, elMessageBoxConfirmSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  elMessageBoxConfirmSpy: vi.fn()
}))

vi.mock('@/api/system/settings', () => ({
  listSettings: vi.fn(),
  updateSettings: vi.fn(),
  settingErrorMessage: vi.fn(() => '后端说：值不合法'),
  settingErrorStatus: vi.fn(() => null)
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params === undefined ? key : `${key} ${JSON.stringify(params)}`
  })
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载
// （同 ZBanRules.spec.ts 的处理）。桩只做到「能改值、能点按钮」这一层。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElButton = defineComponent({
    name: 'ElButton',
    props: {
      type: String,
      size: String,
      loading: Boolean,
      disabled: Boolean,
      icon: { type: [Object, String, Function], default: undefined }
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
              if (!props.disabled) {
                emit('click')
              }
            }
          },
          slots.default?.()
        )
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: String, default: '' }, size: String, clearable: Boolean },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElInputNumber = defineComponent({
    name: 'ElInputNumber',
    props: {
      modelValue: { type: Number, default: undefined },
      min: { type: Number, default: undefined },
      max: { type: Number, default: undefined },
      size: String
    },
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
    props: {
      modelValue: { type: [String, Number, Array], default: '' },
      size: String,
      multiple: Boolean,
      filterable: Boolean,
      allowCreate: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-select-stub',
          value: Array.isArray(props.modelValue) ? props.modelValue.join(',') : props.modelValue,
          onInput: (event: Event) =>
            emit(
              'update:modelValue',
              props.multiple
                ? (event.target as HTMLInputElement).value.split(',').filter(Boolean)
                : (event.target as HTMLInputElement).value
            )
        })
    }
  })

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: {
      modelValue: { type: Boolean, default: false },
      size: String,
      activeText: String,
      inactiveText: String
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('button', {
          class: 'el-switch-stub',
          'data-checked': props.modelValue ? 'true' : 'false',
          onClick: () => emit('update:modelValue', !props.modelValue)
        })
    }
  })

  const ElEmpty = defineComponent({
    name: 'ElEmpty',
    props: { description: String, imageSize: Number },
    setup(props, { slots }) {
      return () =>
        h('div', { class: 'el-empty-stub' }, [props.description, slots.extra?.()])
    }
  })

  return {
    ElButton,
    ElInput,
    ElInputNumber,
    ElSelect,
    ElSwitch,
    ElEmpty,
    ElOption: defineComponent({
      name: 'ElOption',
      props: { label: String, value: { type: [String, Number], default: undefined } },
      setup(props) {
        return () => h('span', { class: 'el-option-stub' }, props.label)
      }
    }),
    ElTooltip: defineComponent({
      name: 'ElTooltip',
      props: { content: String, placement: String },
      setup(_, { slots }) {
        return () => h('span', { class: 'el-tooltip-stub' }, slots.default?.())
      }
    }),
    ElIcon: defineComponent({
      name: 'ElIcon',
      setup(_, { slots }) {
        return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
      }
    }),
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: Object.assign(vi.fn(), {
      confirm: elMessageBoxConfirmSpy
    }),
    // v-loading 由 unplugin-vue-components 的 ElementPlusResolver 从本包按需引入
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} }
  }
})

const SETTINGS: SystemSettingsData = {
  items: [
    {
      key: 'ai.rate_limit_per_minute',
      value: 60,
      type: 'int',
      group: 'ai',
      label: '每分钟请求上限',
      description: '单用户每分钟最多发起多少次 AI 请求',
      min: 1,
      max: 600,
      options: null,
      updated_at: null,
      updated_by: null
    },
    {
      key: 'ai.role_quota',
      value: { admin: { ocr: 100, chat: 200 } },
      type: 'dict',
      group: 'ai',
      label: '角色月度额度',
      description: '按角色限制每月可用次数',
      min: null,
      max: null,
      options: null,
      updated_at: '2026-10-26T10:00:00',
      updated_by: '6a1b'
    },
    {
      key: 'ops.log_retention_days',
      value: 30,
      type: 'int',
      group: 'ops',
      label: '日志保留天数',
      description: null,
      min: 1,
      max: 365,
      options: null,
      updated_at: null,
      updated_by: null
    }
  ],
  defaults: {
    'ai.rate_limit_per_minute': 60,
    'ai.role_quota': { admin: { ocr: 100, chat: 200 } },
    'ops.log_retention_days': 30
  }
}

function mountPage(): VueWrapper {
  return mount(ZSystemSettings)
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper
    .findAll('.el-button-stub')
    .find((candidate) => candidate.text().includes(text))
  if (!button) {
    throw new Error(`找不到按钮：${text}`)
  }
  return button
}

beforeEach(() => {
  vi.mocked(listSettings).mockReset()
  vi.mocked(updateSettings).mockReset()
  vi.mocked(settingErrorMessage).mockReset()
  vi.mocked(settingErrorStatus).mockReset()
  vi.mocked(settingErrorMessage).mockReturnValue('后端说：值不合法')
  vi.mocked(settingErrorStatus).mockReturnValue(null)
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')
  vi.mocked(listSettings).mockResolvedValue(SETTINGS)
  vi.mocked(updateSettings).mockResolvedValue(SETTINGS)
})

describe('设置页', () => {
  it('按分组渲染设置项，标签与说明都出得来', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('manage.settings.groups.ai')
    expect(wrapper.text()).toContain('manage.settings.groups.ops')
    expect(wrapper.text()).toContain('每分钟请求上限')
    expect(wrapper.text()).toContain('单用户每分钟最多发起多少次 AI 请求')
    expect(wrapper.text()).toContain('日志保留天数')
  })

  it('没有改动时保存按钮不可用', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(buttonByText(wrapper, 'manage.settings.save').attributes('disabled')).toBeDefined()
  })

  it('改了值才出现待保存标记，并解除保存按钮的禁用', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.findAll('.el-input-number-stub')[0]!.setValue('120')
    await flushPromises()

    expect(wrapper.text()).toContain('manage.settings.pending')
    expect(buttonByText(wrapper, 'manage.settings.save').attributes('disabled')).toBeUndefined()
  })

  it('保存只提交改动的那一个键', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.findAll('.el-input-number-stub')[0]!.setValue('120')
    await flushPromises()

    await buttonByText(wrapper, 'manage.settings.save').trigger('click')
    await flushPromises()

    expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
    expect(vi.mocked(updateSettings)).toHaveBeenCalledWith([
      { key: 'ai.rate_limit_per_minute', value: 120 }
    ])
    expect(elMessageSuccessSpy).toHaveBeenCalled()
  })

  it('保存失败时按后端给的话提示，不说「操作失败」', async () => {
    vi.mocked(updateSettings).mockRejectedValue(new Error('boom'))
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.findAll('.el-input-number-stub')[0]!.setValue('120')
    await flushPromises()

    await buttonByText(wrapper, 'manage.settings.save').trigger('click')
    await flushPromises()

    expect(elMessageErrorSpy).toHaveBeenCalledWith('后端说：值不合法')
  })

  it('403 给出明确的权限说明，不是空白页', async () => {
    vi.mocked(listSettings).mockRejectedValue(new Error('forbidden'))
    vi.mocked(settingErrorStatus).mockReturnValue(403)
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('manage.settings.forbidden.title')
    expect(wrapper.text()).toContain('manage.settings.forbidden.desc')
    expect(wrapper.findAll('.setting-row')).toHaveLength(0)
  })

  it('加载失败时给出重试入口', async () => {
    vi.mocked(listSettings).mockRejectedValue(new Error('network'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('manage.settings.loadFailed')
    expect(buttonByText(wrapper, 'manage.settings.refresh').exists()).toBe(true)
  })

  it('角色额度表渲染行与列，并能加一行', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const rows = wrapper.findAll('.role-quota__table tbody tr')
    expect(rows).toHaveLength(1)
    const roleInput = wrapper.find('.role-quota__col-role .el-select-stub')
    expect((roleInput.element as HTMLInputElement).value).toBe('admin')

    await buttonByText(wrapper, 'manage.settings.roleQuota.addRow').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.role-quota__table tbody tr')).toHaveLength(2)
  })

  it('扁平 dict 渲染成键值行，改值后只提交改动的键', async () => {
    // 后端的 ai.rate_limit_per_minute 是扁平 dict（{ ocr: 10, chat: 20 }），
    // 契约类型里 dict 只声明了角色额度那一种形状，这里按运行时的真实形状造数据。
    vi.mocked(listSettings).mockResolvedValue({
      items: [
        {
          key: 'ai.rate_limit_per_minute',
          value: { ocr: 10, chat: 20 } as unknown as SystemSettingsData['items'][0]['value'],
          type: 'dict',
          group: 'ai',
          label: 'AI 每分钟调用上限',
          description: '按能力（ocr/chat）的每用户每分钟上限',
          min: 0,
          max: 1_000_000,
          options: null,
          updated_at: null,
          updated_by: null
        },
        SETTINGS.items[1]!
      ],
      defaults: {}
    })

    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('.flat-dict__row')).toHaveLength(2)
    // i18n 在测试里被 mock 成「原文返回」，kindLabel 认不出翻译就退回原键名
    expect(wrapper.findAll('.flat-dict__key').map((node) => node.text())).toEqual(['ocr', 'chat'])
    // 嵌套 dict（角色额度）仍然走表格，没有被扁平分支吃掉
    expect(wrapper.findAll('.role-quota__table tbody tr')).toHaveLength(1)

    await wrapper.findAll('.flat-dict__row .el-input-number-stub')[1]!.setValue('50')
    await flushPromises()

    await buttonByText(wrapper, 'manage.settings.save').trigger('click')
    await flushPromises()

    expect(vi.mocked(updateSettings)).toHaveBeenCalledWith([
      { key: 'ai.rate_limit_per_minute', value: { ocr: 10, chat: 50 } }
    ])
  })
})
