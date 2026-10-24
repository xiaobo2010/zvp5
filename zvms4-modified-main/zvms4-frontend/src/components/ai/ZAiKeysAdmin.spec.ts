import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiKeysAdmin from './ZAiKeysAdmin.vue'
import { createKey, listKeys, updateKey } from '@/api/ai'
import { AiRequestError } from '@/api/ai/errors'
import type { AiKeyRecord } from '@/../types/ai'

const { elMessageErrorSpy, elMessageSuccessSpy, elMessageBoxConfirmSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  elMessageBoxConfirmSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listKeys: vi.fn(),
    createKey: vi.fn(),
    updateKey: vi.fn(),
    deleteKey: vi.fn()
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

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, Fragment, h, ref } = await import('vue')

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
          .map((node) => node as { props: Record<string, unknown>; children: Record<string, unknown> })
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

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: { modelValue: { type: Boolean, default: false }, disabled: Boolean },
    emits: ['change'],
    setup(props, { emit }) {
      return () =>
        h(
          'button',
          {
            class: 'el-switch-stub',
            'data-checked': props.modelValue ? 'true' : 'false',
            onClick: () => {
              if (!props.disabled) {
                emit('change', !props.modelValue)
              }
            }
          },
          ''
        )
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

  // 表单桩会真的跑一遍 rules：校验逻辑本身就是被测对象之一。
  const ElForm = defineComponent({
    name: 'ElForm',
    props: { model: Object, rules: Object },
    setup(props, { slots, expose }) {
      expose({
        validate: async () => {
          const model = (props.model ?? {}) as Record<string, unknown>
          const rules = (props.rules ?? {}) as Record<string, unknown[]>
          for (const [field, fieldRules] of Object.entries(rules)) {
            for (const rule of fieldRules) {
              const validator = (rule as { validator?: (...args: unknown[]) => void }).validator
              if (typeof validator !== 'function') {
                continue
              }
              let message = ''
              await new Promise<void>((resolve) => {
                validator(rule, model[field], (error?: Error) => {
                  if (error) {
                    message = error.message
                  }
                  resolve()
                })
              })
              if (message) {
                return false
              }
            }
          }
          return true
        },
        clearValidate: () => undefined,
        resetFields: () => undefined
      })
      return () => h('form', { class: 'el-form-stub' }, slots.default?.())
    }
  })

  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    props: { prop: String, label: String },
    setup(props, { slots }) {
      return () => h('div', { class: 'el-form-item-stub', 'data-prop': props.prop }, slots.default?.())
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

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: [String, Number] },
    setup() {
      return () => null
    }
  })

  const ElDatePicker = defineComponent({
    name: 'ElDatePicker',
    props: {
      modelValue: { type: [Date, Array, String, Number], default: null },
      type: String,
      placeholder: String
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-date-picker-stub',
          value: props.modelValue instanceof Date ? props.modelValue.toISOString() : '',
          placeholder: props.placeholder,
          onInput: (event: Event) => {
            const raw = (event.target as HTMLInputElement).value
            if (!raw) {
              emit('update:modelValue', null)
              return
            }
            const parsed = new Date(raw)
            emit('update:modelValue', Number.isNaN(parsed.getTime()) ? null : parsed)
          }
        })
    }
  })

  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('i', { class: 'el-icon-stub' }, slots.default?.())
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
    ElSwitch,
    ElButton,
    ElDialog,
    ElForm,
    ElFormItem,
    ElInput,
    ElSelect,
    ElOption,
    ElDatePicker,
    ElIcon,
    ElEmpty,
    ElTag,
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

const personalOther: AiKeyRecord = {
  oid: 'oid-personal-other',
  scope: 'personal',
  owner_oid: 'someone-else',
  name: 'other-person',
  kind: 'openai_compat',
  base_url: 'https://other.example.com',
  models: ['gpt-4o-mini'],
  headers: {},
  api_path: null,
  key_hint: '1111',
  enabled: true,
  expires_at: null,
  created_at: '2026-10-24T00:00:00',
  updated_at: '2026-10-24T00:00:00',
  owned: false
}

const shared: AiKeyRecord = {
  oid: 'oid-shared',
  scope: 'shared',
  owner_oid: 'platform',
  name: 'shared-key',
  kind: 'openai_compat',
  base_url: 'https://api.example.com',
  models: ['gpt-4o-mini', 'gpt-4o'],
  headers: { 'X-Api-Key': '****' },
  api_path: '/v1/chat/completions',
  key_hint: '2222',
  enabled: true,
  expires_at: null,
  created_at: '2026-10-24T00:00:00',
  updated_at: '2026-10-24T00:00:00',
  owned: false
}

const personalMine: AiKeyRecord = {
  ...personalOther,
  oid: 'oid-personal-mine',
  owner_oid: 'me',
  name: 'my-key',
  key_hint: '3333',
  owned: true
}

function mountPage(): VueWrapper {
  return mount(ZAiKeysAdmin)
}

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

beforeEach(() => {
  vi.mocked(listKeys).mockReset()
  vi.mocked(createKey).mockReset()
  vi.mocked(updateKey).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  vi.mocked(listKeys).mockResolvedValue([personalOther, shared, personalMine])
})

describe('Key 管理', () => {
  it('渲染凭据列表与类型徽标', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.find('.el-table-stub')
    expect(table.text()).toContain('shared-key')
    expect(table.text()).toContain('my-key')
    expect(table.text()).toContain('2222')
    expect(table.text()).toContain('ai.keys.scopeShared')
    expect(table.text()).toContain('ai.keys.scopePersonal')

    wrapper.unmount()
  })

  it('他人的个人凭据没有操作按钮，共享与本人凭据有', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const rows = wrapper.findAll('.el-table-stub__row')
    const otherRow = rows.find((row) => row.text().includes('other-person'))!
    const sharedRow = rows.find((row) => row.text().includes('shared-key'))!
    const mineRow = rows.find((row) => row.text().includes('my-key'))!

    expect(otherRow.text()).not.toContain('ai.common.edit')
    expect(otherRow.text()).not.toContain('ai.common.delete')
    expect(otherRow.text()).toContain('ai.keys.readOnlyHint')

    expect(sharedRow.text()).toContain('ai.common.edit')
    expect(mineRow.text()).toContain('ai.common.delete')

    wrapper.unmount()
  })

  it('新建时名称不合规则不发请求', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, 'ai.keys.create')!.trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    expect(dialog.exists()).toBe(true)

    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('Bad Name')
    await inputs[1]!.setValue('https://api.example.com')
    await inputs[3]!.setValue('sk-1234567890')

    await findButton(wrapper, 'ai.common.create')!.trigger('click')
    await flushPromises()

    expect(createKey).not.toHaveBeenCalled()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('新建时密钥太短也不发请求', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, 'ai.keys.create')!.trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('demo-key')
    await inputs[1]!.setValue('https://api.example.com')
    await inputs[3]!.setValue('short')

    await findButton(wrapper, 'ai.common.create')!.trigger('click')
    await flushPromises()

    expect(createKey).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('新建成功时按后端字段提交', async () => {
    vi.mocked(createKey).mockResolvedValue(shared)
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, 'ai.keys.create')!.trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('demo-key')
    await inputs[1]!.setValue('https://api.example.com')
    await inputs[2]!.setValue('gpt-4o-mini')
    await inputs[3]!.setValue('sk-1234567890')

    await findButton(wrapper, 'ai.common.create')!.trigger('click')
    await flushPromises()

    expect(createKey).toHaveBeenCalledWith({
      scope: 'shared',
      name: 'demo-key',
      base_url: 'https://api.example.com',
      models: ['gpt-4o-mini'],
      api_key: 'sk-1234567890',
      enabled: true
    })
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.keys.createdNotice')

    wrapper.unmount()
  })

  it('重名冲突落到名称字段上', async () => {
    vi.mocked(createKey).mockRejectedValue(
      new AiRequestError({ status: 409, code: 'AI_KEY_DUPLICATE' })
    )
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, 'ai.keys.create')!.trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('demo-key')
    await inputs[1]!.setValue('https://api.example.com')
    await inputs[3]!.setValue('sk-1234567890')

    await findButton(wrapper, 'ai.common.create')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-dialog-stub').text()).toContain('AI_KEY_DUPLICATE')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('列表加载失败时给出空态提示', async () => {
    vi.mocked(listKeys).mockRejectedValue(new Error('forbidden'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.el-empty-stub').text()).toBe('ai.keys.loadFailed')

    wrapper.unmount()
  })
})

describe('Key 到期', () => {
  const expired: AiKeyRecord = {
    ...shared,
    oid: 'oid-expired',
    name: 'expired-key',
    expires_at: '2020-01-01T00:00:00Z'
  }

  it('到期列区分永久与已过期', async () => {
    vi.mocked(listKeys).mockResolvedValue([shared, expired])
    const wrapper = mountPage()
    await flushPromises()

    const rows = wrapper.findAll('.el-table-stub__row')
    const permanentRow = rows.find((row) => row.text().includes('shared-key'))!
    const expiredRow = rows.find((row) => row.text().includes('expired-key'))!

    expect(permanentRow.text()).toContain('ai.keys.neverExpires')
    expect(permanentRow.text()).not.toContain('ai.keys.expired')

    expect(expiredRow.text()).toContain('ai.keys.expired')
    expect(expiredRow.find('[data-expired="true"]').exists()).toBe(true)
    expect(expiredRow.text()).toContain('2020-01-01')

    wrapper.unmount()
  })

  it('编辑时清空到期时间会显式传 null', async () => {
    const withExpiry: AiKeyRecord = { ...shared, expires_at: '2030-01-01T00:00:00Z' }
    vi.mocked(listKeys).mockResolvedValue([withExpiry])
    vi.mocked(updateKey).mockResolvedValue(withExpiry)

    const wrapper = mountPage()
    await flushPromises()

    const row = wrapper.findAll('.el-table-stub__row')[0]!
    const editButton = row.findAll('button').find((button) => button.text() === 'ai.common.edit')!
    await editButton.trigger('click')

    const dialog = wrapper.find('.el-dialog-stub')
    const picker = dialog.find('.el-date-picker-stub')
    expect(picker.exists()).toBe(true)
    await picker.setValue('')

    await findButton(wrapper, 'ai.common.save')!.trigger('click')
    await flushPromises()

    expect(updateKey).toHaveBeenCalledWith('oid-shared', expect.objectContaining({ expires_at: null }))

    wrapper.unmount()
  })

  it('编辑时保留原有到期时间', async () => {
    const withExpiry: AiKeyRecord = { ...shared, expires_at: '2030-01-01T00:00:00Z' }
    vi.mocked(listKeys).mockResolvedValue([withExpiry])
    vi.mocked(updateKey).mockResolvedValue(withExpiry)

    const wrapper = mountPage()
    await flushPromises()

    const row = wrapper.findAll('.el-table-stub__row')[0]!
    const editButton = row.findAll('button').find((button) => button.text() === 'ai.common.edit')!
    await editButton.trigger('click')

    await findButton(wrapper, 'ai.common.save')!.trigger('click')
    await flushPromises()

    expect(updateKey).toHaveBeenCalledWith(
      'oid-shared',
      expect.objectContaining({ expires_at: '2030-01-01T00:00:00.000Z' })
    )

    wrapper.unmount()
  })

  it('到期时间不合法时错误落到到期字段上', async () => {
    vi.mocked(createKey).mockRejectedValue(
      new AiRequestError({ status: 400, code: 'AI_KEY_BAD_EXPIRY' })
    )
    const wrapper = mountPage()
    await flushPromises()

    await findButton(wrapper, 'ai.keys.create')!.trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('demo-key')
    await inputs[1]!.setValue('https://api.example.com')
    await inputs[3]!.setValue('sk-1234567890')

    await findButton(wrapper, 'ai.common.create')!.trigger('click')
    await flushPromises()

    const expiresItem = wrapper.find('[data-prop="expiresAt"]')
    expect(expiresItem.text()).toContain('AI_KEY_BAD_EXPIRY')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    wrapper.unmount()
  })
})
