import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZBanRules from './ZBanRules.vue'
import {
  createRule,
  deleteRule,
  getBannedDevices,
  unbanDevice,
  getBannedUsers,
  getRules,
  getTriggers,
  getViolations,
  updateRule
} from '@/api/ban'
import type { BannedDevice, BannedUser, BanRule, BanTrigger, BanViolation } from '@/api/ban'

const { elMessageErrorSpy, elMessageSuccessSpy, elMessageInfoSpy, elMessageBoxConfirmSpy } =
  vi.hoisted(() => ({
    elMessageErrorSpy: vi.fn(),
    elMessageSuccessSpy: vi.fn(),
    elMessageInfoSpy: vi.fn(),
    elMessageBoxConfirmSpy: vi.fn()
  }))

vi.mock('@/api/ban', () => ({
  getRules: vi.fn(),
  updateRule: vi.fn(),
  getBannedUsers: vi.fn(),
  getTriggers: vi.fn(),
  createRule: vi.fn(),
  deleteRule: vi.fn(),
  getViolations: vi.fn(),
  getBannedDevices: vi.fn(),
  banDevice: vi.fn(),
  unbanDevice: vi.fn(),
  resetDeviceGradient: vi.fn()
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

// element-plus 整包 mock:真实包会连带引入 theme-chalk 的 .css,node ESM 下无法加载
// (同 ServerSsh.spec.ts 的处理)。表格/开关/表单桩做成可交互的最小实现。
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
        const renderCell = (def: { props: Record<string, unknown>; slots: Record<string, unknown> }, row: Record<string, unknown>) => {
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
    props: { modelValue: { type: Boolean, default: false } },
    emits: ['change'],
    setup(props, { emit }) {
      return () =>
        h(
          'button',
          {
            class: 'el-switch-stub',
            'data-checked': props.modelValue ? 'true' : 'false',
            onClick: () => emit('change', !props.modelValue)
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
          ? h(
              'div',
              { class: 'el-dialog-stub', 'data-title': props.title },
              [
                ...(slots.default?.() ?? []),
                ...(slots.footer?.() ?? [])
              ]
            )
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

  const ElInputNumber = defineComponent({
    name: 'ElInputNumber',
    props: { modelValue: { type: Number, default: undefined } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-number-stub',
          type: 'number',
          value: props.modelValue,
          onInput: (event: Event) => {
            const raw = (event.target as HTMLInputElement).value
            emit('update:modelValue', raw === '' ? undefined : Number(raw))
          }
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
    ElInputNumber,
    ElEmpty,
    ElTag,
    ElSelect,
    ElOption,
    // v-loading 由 unplugin-vue-components 的 ElementPlusResolver 从本包按需引入
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

const ruleFixture: BanRule = {
  code: 'foreign_device_login',
  name: '非主学海设备登录',
  trigger: 'foreign_device_login',
  trigger_name: '非主学海设备登录',
  enabled: true,
  builtin: true,
  config: { threshold: 3, window_hours: 24 },
  penalties: [1, 2, 7],
  updated_at: '2026-10-20T12:00:00'
}

const triggerFixtures: BanTrigger[] = [
  {
    trigger: 'foreign_device_login',
    name: '非主学海设备登录',
    evaluation: 'login',
    description: '非主绑定的学海设备登录时记一次违规',
    config: { threshold: 3, window_hours: 24 }
  },
  {
    trigger: 'platform_misuse',
    name: '平台违规使用',
    evaluation: 'report',
    description: '客户端上报的嵌入/调试/桥缺失信号累计计数',
    config: { threshold: 3, window_hours: 24 }
  }
]

const violationFixture: BanViolation = {
  _id: 'violation-1',
  user_oid: '69b7a4ad0243d973f1e1a2c1',
  user_name: '王违规',
  user_id: '20241010',
  trigger: 'platform_misuse',
  signal: 'iframe',
  detail: 'top!=self',
  xuehai_id: 'xh0001',
  created_at: '2026-10-21T09:30:00'
}

const bannedUserFixtures: BannedUser[] = [
  {
    oid: '69b7a4ad0243d973f1e1a2b3',
    name: '张三',
    user_id: '20240801',
    class_name: '高三8班',
    reason: '同学号多设备登录',
    permanent: false,
    until: '2026-10-21T08:00:00',
    source: 'rule',
    rule_code: 'multi_device_login'
  },
  {
    oid: '69b7a4ad0243d973f1e1a2b4',
    name: '李四',
    user_id: '20240002',
    class_name: '高三8班',
    reason: '管理员封禁',
    permanent: true,
    until: null,
    source: 'admin',
    rule_code: null
  }
]

function mockApi(options?: {
  rules?: BanRule[]
  users?: BannedUser[]
  usersSize?: number
  violations?: BanViolation[]
  violationsSize?: number
  updateRejects?: boolean
}) {
  // 组件会就地改写 rule.enabled(乐观更新),fixture 必须深拷贝避免用例间串扰
  vi.mocked(getRules).mockResolvedValue(
    options?.rules ?? [structuredClone(ruleFixture)]
  )
  vi.mocked(getTriggers).mockResolvedValue(structuredClone(triggerFixtures))
  vi.mocked(getViolations).mockResolvedValue({
    violations: options?.violations ?? [structuredClone(violationFixture)],
    size: options?.violationsSize ?? 1
  })
  vi.mocked(getBannedUsers).mockResolvedValue({
    users: options?.users ?? structuredClone(bannedUserFixtures),
    size: options?.usersSize ?? bannedUserFixtures.length
  })
  if (options?.updateRejects) {
    vi.mocked(updateRule).mockRejectedValue(new Error('forbidden'))
  } else {
    vi.mocked(updateRule).mockImplementation(async (code, payload) => ({
      ...ruleFixture,
      code,
      enabled: payload.enabled ?? ruleFixture.enabled,
      penalties: payload.penalties ?? ruleFixture.penalties
    }))
  }
  vi.mocked(createRule).mockImplementation(
    async (payload) =>
      ({
        ...ruleFixture,
        builtin: false,
        ...payload,
        updated_at: '2026-10-21T12:00:00'
      }) as BanRule
  )
  vi.mocked(deleteRule).mockResolvedValue(true)
  vi.mocked(getBannedDevices).mockResolvedValue({ devices: [], total: 0 })
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')
}

function mountPage(): VueWrapper {
  return mount(ZBanRules)
}

/** 切换页内二级分区（规则与违规 / 账号封禁 / 设备封禁） */
async function openSection(wrapper: VueWrapper, section: 'rules' | 'accounts' | 'devices') {
  const button = wrapper.find(`[data-section="${section}"]`)
  expect(button.exists()).toBe(true)
  await button.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.mocked(getRules).mockReset()
  vi.mocked(getBannedUsers).mockReset()
  vi.mocked(updateRule).mockReset()
  vi.mocked(getTriggers).mockReset()
  vi.mocked(createRule).mockReset()
  vi.mocked(deleteRule).mockReset()
  vi.mocked(getViolations).mockReset()
  vi.mocked(getBannedDevices).mockReset()
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
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('renders rules from the API in a data-driven list', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  const rulesTable = wrapper.findAll('.el-table-stub')[0]!
  const rows = rulesTable.findAll('.el-table-stub__row')
  expect(rows.length).toBe(1)
  expect(wrapper.text()).toContain('非主学海设备登录')
  expect(wrapper.text()).toContain('{"threshold":3,"window_hours":24}')
  expect(wrapper.text()).toContain(
    'manage.banRules.rule.penaltyDay {"days":1} → manage.banRules.rule.penaltyDay {"days":2} → manage.banRules.rule.penaltyDay {"days":7}'
  )
  expect(rulesTable.find('.el-switch-stub').attributes('data-checked')).toBe('true')
  expect(rulesTable.find('.el-table-stub__empty').exists()).toBe(false)

  wrapper.unmount()
})

it('toggling the enabled switch calls updateRule and reflects the new state', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  await wrapper.find('.el-switch-stub').trigger('click')
  await flushPromises()

  expect(updateRule).toHaveBeenCalledWith('foreign_device_login', { enabled: false }, 'verify-token')
  expect(wrapper.find('.el-switch-stub').attributes('data-checked')).toBe('false')

  wrapper.unmount()
})

it('rolls the switch back and shows an error when the update fails', async () => {
  mockApi({ updateRejects: true })
  const wrapper = mountPage()
  await flushPromises()

  await wrapper.find('.el-switch-stub').trigger('click')
  await flushPromises()

  expect(updateRule).toHaveBeenCalledWith('foreign_device_login', { enabled: false }, 'verify-token')
  expect(wrapper.find('.el-switch-stub').attributes('data-checked')).toBe('true')
  expect(elMessageErrorSpy).toHaveBeenCalledWith('manage.banRules.revertedNotice')

  wrapper.unmount()
})

it('submits edited rule config and penalties via updateRule', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  const editButton = wrapper
    .findAll('button')
    .find((button) => button.text() === 'manage.banRules.rule.edit')
  expect(editButton).toBeDefined()
  await editButton!.trigger('click')

  const dialog = wrapper.find('.el-dialog-stub')
  expect(dialog.exists()).toBe(true)
  expect(dialog.attributes('data-title')).toBe(
    'manage.banRules.dialog.title {"name":"非主学海设备登录"}'
  )

  const numberInputs = dialog.findAll('.el-input-number-stub')
  expect(numberInputs.length).toBe(2)
  await numberInputs[0]!.setValue('4')
  await numberInputs[1]!.setValue('48')
  await dialog.find('.el-input-stub').setValue('2,5')

  const saveButton = wrapper
    .findAll('button')
    .find((button) => button.text() === 'manage.banRules.dialog.save')
  await saveButton!.trigger('click')
  await flushPromises()

  expect(updateRule).toHaveBeenCalledWith(
    'foreign_device_login',
    {
      config: { threshold: 4, window_hours: 48 },
      penalties: [2, 5]
    },
    'verify-token'
  )
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.banRules.savedNotice')
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)

  wrapper.unmount()
})

it('renders banned users with pagination and re-queries on page change', async () => {
  mockApi({ usersSize: 25 })
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'accounts')

  expect(getBannedUsers).toHaveBeenCalledWith(1, 20, '')
  expect(wrapper.text()).toContain('张三')
  expect(wrapper.text()).toContain('高三8班')
  expect(wrapper.text()).toContain('manage.banRules.users.permanent')

  const pagination = wrapper.findAll('.z-pagination-stub')[0]!
  expect(pagination.attributes('data-total')).toBe('25')
  expect(pagination.attributes('data-page-size')).toBe('20')

  await pagination.trigger('click')
  await flushPromises()

  expect(getBannedUsers).toHaveBeenLastCalledWith(2, 20, '')

  wrapper.unmount()
})

it('searches banned users by name or student id', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'accounts')

  const searchInput = wrapper.findAll('.el-input-stub')[0]!
  expect(searchInput.exists()).toBe(true)
  await searchInput.setValue('张三')

  const searchButton = wrapper
    .findAll('button')
    .find((button) => button.text() === 'manage.banRules.users.search')
  expect(searchButton).toBeDefined()
  await searchButton!.trigger('click')
  await flushPromises()

  expect(getBannedUsers).toHaveBeenLastCalledWith(1, 20, '张三')

  wrapper.unmount()
})

it('shows a clear empty state when no banned users exist', async () => {
  mockApi({ users: [], usersSize: 0 })
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'accounts')

  const empties = wrapper.findAll('.el-empty-stub')
  expect(empties.length).toBe(1)
  expect(empties[0]!.text()).toBe('manage.banRules.users.empty')
  expect(wrapper.text()).not.toContain('张三')

  wrapper.unmount()
})

it('falls back to the legacy-ban label when source is null and never prints an empty 规则 prefix', async () => {
  mockApi({
    users: [
      {
        oid: '69b7a4ad0243d973f1e1a2b5',
        name: '王二',
        user_id: '20240802',
        class_name: '高三8班',
        reason: null,
        permanent: true,
        until: null,
        source: null,
        rule_code: null
      }
    ] as BannedUser[],
    usersSize: 1
  })
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'accounts')

  const usersTable = wrapper.findAll('.el-table-stub')[0]!
  expect(usersTable.text()).toContain('manage.banRules.users.sourceUnknown')
  expect(usersTable.text()).toContain('manage.banRules.users.reasonUnknown')
  expect(usersTable.text()).not.toBe('manage.banRules.users.sourceRule')

  wrapper.unmount()
})

it('falls back to the legacy-ban label when source is rule without rule_code', async () => {
  mockApi({
    users: [
      {
        oid: '69b7a4ad0243d973f1e1a2b6',
        name: '赵一',
        user_id: '20240803',
        class_name: '高三8班',
        reason: '同学号多设备登录',
        permanent: true,
        until: null,
        source: 'rule',
        rule_code: null
      }
    ] as BannedUser[],
    usersSize: 1
  })
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'accounts')

  expect(wrapper.findAll('.el-table-stub')[0]!.text()).toContain(
    'manage.banRules.users.sourceUnknown'
  )
  expect(wrapper.text()).not.toContain('manage.banRules.users.sourceRule {')

  wrapper.unmount()
})

it('shows empty states when both endpoints fail (e.g. 403)', async () => {
  vi.mocked(getRules).mockRejectedValue(new Error('forbidden'))
  vi.mocked(getViolations).mockRejectedValue(new Error('forbidden'))
  vi.mocked(getBannedUsers).mockRejectedValue(new Error('forbidden'))
  const wrapper = mountPage()
  await flushPromises()

  const empties = wrapper.findAll('.el-empty-stub')
  expect(empties.length).toBe(2)
  expect(empties.every((node) => node.text() === 'manage.banRules.loadFailed')).toBe(true)

  await openSection(wrapper, 'accounts')
  const accountEmpties = wrapper.findAll('.el-empty-stub')
  expect(accountEmpties.length).toBe(1)
  expect(accountEmpties[0]!.text()).toBe('manage.banRules.loadFailed')

  wrapper.unmount()
})

const permanentRuleFixture: BanRule = {
  ...structuredClone(ruleFixture),
  code: 'permanent_gradient',
  name: '永久梯度规则',
  penalties: [1, 3, null]
}

function findButton(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

async function openEditDialog(wrapper: VueWrapper) {
  await findButton(wrapper, 'manage.banRules.rule.edit')!.trigger('click')
  return wrapper.find('.el-dialog-stub')
}

async function submitPenaltiesInput(wrapper: VueWrapper, text: string) {
  const dialog = wrapper.find('.el-dialog-stub')
  await dialog.find('.el-input-stub').setValue(text)
  await findButton(wrapper, 'manage.banRules.dialog.save')!.trigger('click')
  await flushPromises()
}

it('keeps the permanent grade when saving an untouched gradient', async () => {
  mockApi({ rules: [structuredClone(permanentRuleFixture)] })
  const wrapper = mountPage()
  await flushPromises()

  const dialog = await openEditDialog(wrapper)
  expect((dialog.find('.el-input-stub').element as HTMLInputElement).value).toBe('1,3,0')

  await findButton(wrapper, 'manage.banRules.dialog.save')!.trigger('click')
  await flushPromises()

  expect(updateRule).toHaveBeenCalledWith(
    'permanent_gradient',
    {
      config: { threshold: 3, window_hours: 24 },
      penalties: [1, 3, null]
    },
    'verify-token'
  )
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.banRules.savedNotice')

  wrapper.unmount()
})

it('renders the permanent grade without leaking null', async () => {
  mockApi({ rules: [structuredClone(permanentRuleFixture)] })
  const wrapper = mountPage()
  await flushPromises()

  const rulesTable = wrapper.findAll('.el-table-stub')[0]!
  expect(rulesTable.text()).toContain('manage.banRules.rule.penaltyPermanent')
  expect(rulesTable.text()).not.toContain('null')

  wrapper.unmount()
})

it('accepts 0 as the permanent grade only in the last position', async () => {
  mockApi({ rules: [structuredClone(permanentRuleFixture)] })
  const wrapper = mountPage()
  await flushPromises()

  await openEditDialog(wrapper)
  await submitPenaltiesInput(wrapper, '0')
  expect(updateRule).toHaveBeenLastCalledWith(
    'permanent_gradient',
    {
      config: { threshold: 3, window_hours: 24 },
      penalties: [null]
    },
    'verify-token'
  )

  await openEditDialog(wrapper)
  await submitPenaltiesInput(wrapper, '1,0')
  expect(updateRule).toHaveBeenLastCalledWith(
    'permanent_gradient',
    {
      config: { threshold: 3, window_hours: 24 },
      penalties: [1, null]
    },
    'verify-token'
  )

  wrapper.unmount()
})

it('rejects 0 outside the last position or repeated instead of silently fixing it', async () => {
  mockApi({ rules: [structuredClone(permanentRuleFixture)] })
  const wrapper = mountPage()
  await flushPromises()

  await openEditDialog(wrapper)
  for (const text of ['0,1', '0,0']) {
    await submitPenaltiesInput(wrapper, text)
    expect(elMessageErrorSpy).toHaveBeenLastCalledWith(
      'manage.banRules.validation.penaltiesPermanentPosition'
    )
  }

  expect(updateRule).not.toHaveBeenCalled()
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

  wrapper.unmount()
})

it('renders the trigger name column and violation records with signal labels', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  const rulesTable = wrapper.findAll('.el-table-stub')[0]!
  expect(rulesTable.text()).toContain('非主学海设备登录')

  const violationsTable = wrapper.findAll('.el-table-stub')[1]!
  expect(violationsTable.text()).toContain('王违规')
  expect(violationsTable.text()).toContain('20241010')
  expect(violationsTable.text()).toContain('top!=self')
  expect(violationsTable.text()).toContain('manage.banRules.violations.signals.iframe')

  wrapper.unmount()
})

it('shows the violations empty state when there are no records', async () => {
  mockApi({ violations: [], violationsSize: 0 })
  const wrapper = mountPage()
  await flushPromises()

  const empties = wrapper.findAll('.el-empty-stub')
  expect(empties.map((node) => node.text())).toContain('manage.banRules.violations.empty')

  wrapper.unmount()
})

it('creates a rule via createRule from the create dialog', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.banRules.rule.create')!.trigger('click')

  const dialog = wrapper.find('.el-dialog-stub')
  expect(dialog.exists()).toBe(true)
  expect(dialog.attributes('data-title')).toBe('manage.banRules.rule.create')

  const inputs = dialog.findAll('.el-input-stub')
  await inputs[0]!.setValue('迟交作业封号')
  await inputs[1]!.setValue('late_homework')
  await dialog.find('.el-select-stub').setValue('platform_misuse')
  await inputs[2]!.setValue('1,3')

  await findButton(wrapper, 'manage.banRules.dialog.create')!.trigger('click')
  await flushPromises()

  expect(createRule).toHaveBeenCalledWith(
    {
      code: 'late_homework',
      name: '迟交作业封号',
      trigger: 'platform_misuse',
      config: { threshold: 3, window_hours: 24 },
      penalties: [1, 3],
      enabled: true
    },
    'verify-token'
  )
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.banRules.rule.createdNotice')
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)

  wrapper.unmount()
})

it('shows an error and keeps the dialog open when creating fails', async () => {
  mockApi()
  vi.mocked(createRule).mockRejectedValue(new Error('conflict'))
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.banRules.rule.create')!.trigger('click')
  const dialog = wrapper.find('.el-dialog-stub')
  const inputs = dialog.findAll('.el-input-stub')
  await inputs[0]!.setValue('迟交作业封号')
  await inputs[1]!.setValue('late_homework')
  await dialog.find('.el-select-stub').setValue('platform_misuse')
  await inputs[2]!.setValue('1,3')

  await findButton(wrapper, 'manage.banRules.dialog.create')!.trigger('click')
  await flushPromises()

  expect(elMessageErrorSpy).toHaveBeenCalledWith('manage.banRules.rule.createFailed')
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

  wrapper.unmount()
})

it('deletes a non-builtin rule after confirmation and refreshes the list', async () => {
  mockApi({ rules: [{ ...structuredClone(ruleFixture), builtin: false }] })
  const wrapper = mountPage()
  await flushPromises()

  const callsBefore = vi.mocked(getRules).mock.calls.length
  await findButton(wrapper, 'manage.banRules.rule.delete')!.trigger('click')
  await flushPromises()

  expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
  expect(deleteRule).toHaveBeenCalledWith('foreign_device_login', 'verify-token')
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.banRules.rule.deletedNotice')
  expect(vi.mocked(getRules).mock.calls.length).toBeGreaterThan(callsBefore)

  wrapper.unmount()
})

it('does not delete when the confirm dialog is cancelled', async () => {
  mockApi({ rules: [{ ...structuredClone(ruleFixture), builtin: false }] })
  elMessageBoxConfirmSpy.mockRejectedValue('cancel')
  const wrapper = mountPage()
  await flushPromises()

  await findButton(wrapper, 'manage.banRules.rule.delete')!.trigger('click')
  await flushPromises()

  expect(deleteRule).not.toHaveBeenCalled()
  expect(elMessageSuccessSpy).not.toHaveBeenCalled()

  wrapper.unmount()
})

it('disables delete for builtin rules with an explain tooltip', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  const deleteButton = findButton(wrapper, 'manage.banRules.rule.delete')!
  expect(deleteButton.attributes('disabled')).toBeDefined()
  await deleteButton.trigger('click')
  await flushPromises()

  expect(deleteRule).not.toHaveBeenCalled()

  wrapper.unmount()
})

it('defaults to the rules section and keeps the account and device sections closed', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()

  const nav = wrapper.find('.ban-sections')
  expect(nav.exists()).toBe(true)
  const items = nav.findAll('[data-section]')
  expect(items.map((item) => item.attributes('data-section'))).toEqual([
    'rules',
    'accounts',
    'devices'
  ])
  expect(items[0]!.classes()).toContain('is-active')
  expect(items[0]!.attributes('aria-selected')).toBe('true')

  // 默认只渲染规则表与违规记录表，设备列表不提前请求
  expect(wrapper.findAll('.el-table-stub').length).toBe(2)
  expect(wrapper.text()).toContain('manage.banRules.sections.rules')
  expect(getBannedDevices).not.toHaveBeenCalled()

  wrapper.unmount()
})

it('switches to the accounts section and renders the banned users table', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'accounts')

  const active = wrapper
    .findAll('[data-section]')
    .find((item) => item.classes().includes('is-active'))
  expect(active!.attributes('data-section')).toBe('accounts')
  expect(wrapper.findAll('.el-table-stub').length).toBe(1)
  expect(wrapper.text()).toContain('张三')
  expect(getBannedDevices).not.toHaveBeenCalled()

  wrapper.unmount()
})

it('embeds the device bans section and loads the banned devices', async () => {
  mockApi()
  const wrapper = mountPage()
  await flushPromises()
  await openSection(wrapper, 'devices')

  expect(getBannedDevices).toHaveBeenCalledWith(1, 20, '')
  expect(wrapper.find('.device-bans').exists()).toBe(true)
  expect(wrapper.findAll('.el-table-stub').length).toBe(1)
  expect(wrapper.text()).toContain('manage.deviceBans.table.columns.xuehaiId')
  expect(wrapper.text()).toContain('manage.deviceBans.manual.title')
  expect(wrapper.text()).toContain('manage.deviceBans.manual.submit')

  wrapper.unmount()
})

describe('密码再认证', () => {
  it('启停规则先过再认证，令牌过期后换一张重试成功', async () => {
    mockApi()
    reauth.withToken.mockImplementation(
      async (_reason: string, action: (token: string) => Promise<unknown>) => {
        try {
          return await action('stale-token')
        } catch {
          return action('fresh-token')
        }
      }
    )
    vi.mocked(updateRule)
      .mockRejectedValueOnce(Object.assign(new Error('forbidden'), { status: 403 }))
      .mockResolvedValueOnce({ ...ruleFixture, enabled: false })

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-switch-stub').trigger('click')
    await flushPromises()

    expect(reauth.withToken).toHaveBeenCalledWith('manage.banRules.reauthReason', expect.any(Function))
    expect(vi.mocked(updateRule).mock.calls[0]).toEqual([
      'foreign_device_login',
      { enabled: false },
      'stale-token'
    ])
    expect(vi.mocked(updateRule).mock.calls[1]).toEqual([
      'foreign_device_login',
      { enabled: false },
      'fresh-token'
    ])
    expect(wrapper.find('.el-switch-stub').attributes('data-checked')).toBe('false')
    expect(elMessageErrorSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('取消密码框时还原开关并只给一条信息提示', async () => {
    mockApi()
    reauth.withToken.mockRejectedValue('cancel')

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-switch-stub').trigger('click')
    await flushPromises()

    expect(updateRule).not.toHaveBeenCalled()
    expect(wrapper.find('.el-switch-stub').attributes('data-checked')).toBe('true')
    expect(elMessageInfoSpy).toHaveBeenCalledWith('manage.reauth.cancelled')
    expect(elMessageErrorSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})

describe('设备封禁的密码再认证', () => {
  const deviceFixture: BannedDevice = {
    xuehai_id: '100200300',
    reason: '机房多设备登录',
    until: null,
    source: 'admin',
    rule_code: null,
    created_at: '2026-10-20T10:00:00',
    ban_count: 2,
    owner: { id: 'oid-1', name: '张三' }
  }

  it('解封设备先过再认证，令牌过期后换一张重试成功', async () => {
    mockApi()
    vi.mocked(getBannedDevices).mockResolvedValue({ devices: [deviceFixture], total: 1 })
    reauth.withToken.mockImplementation(
      async (_reason: string, action: (token: string) => Promise<unknown>) => {
        try {
          return await action('stale-token')
        } catch {
          return action('fresh-token')
        }
      }
    )
    vi.mocked(unbanDevice)
      .mockRejectedValueOnce(Object.assign(new Error('forbidden'), { status: 403 }))
      .mockResolvedValueOnce(true)

    const wrapper = mountPage()
    await flushPromises()
    await openSection(wrapper, 'devices')

    const unbanButton = wrapper
      .findAll('button')
      .find((button) => button.text() === 'manage.deviceBans.table.unban')
    expect(unbanButton).toBeDefined()
    await unbanButton!.trigger('click')
    await flushPromises()

    expect(reauth.withToken).toHaveBeenCalledWith(
      'manage.deviceBans.reauthReason',
      expect.any(Function)
    )
    expect(vi.mocked(unbanDevice).mock.calls[0]).toEqual(['100200300', 'stale-token'])
    expect(vi.mocked(unbanDevice).mock.calls[1]).toEqual(['100200300', 'fresh-token'])
    expect(elMessageSuccessSpy).toHaveBeenCalledWith(
      'manage.deviceBans.table.unbannedNotice {"xuehaiId":"100200300"}'
    )
    expect(elMessageErrorSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})
