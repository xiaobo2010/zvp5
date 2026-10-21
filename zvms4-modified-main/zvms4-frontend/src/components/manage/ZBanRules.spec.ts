import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ZBanRules from './ZBanRules.vue'
import { getBannedUsers, getRules, updateRule } from '@/api/ban'
import type { BannedUser, BanRule } from '@/api/ban'

const { elMessageErrorSpy, elMessageSuccessSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn()
}))

vi.mock('@/api/ban', () => ({
  getRules: vi.fn(),
  updateRule: vi.fn(),
  getBannedUsers: vi.fn()
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
    setup(_, { emit, slots }) {
      return () =>
        h(
          'button',
          { class: 'el-button-stub', onClick: () => emit('click') },
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
    // v-loading 由 unplugin-vue-components 的 ElementPlusResolver 从本包按需引入
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
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
  code: 'multi_device_login',
  name: '同学号多设备登录',
  enabled: true,
  builtin: true,
  config: { threshold: 3, window_hours: 24 },
  penalties: [1, 2, 7],
  updated_at: '2026-10-20T12:00:00'
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
  updateRejects?: boolean
}) {
  // 组件会就地改写 rule.enabled(乐观更新),fixture 必须深拷贝避免用例间串扰
  vi.mocked(getRules).mockResolvedValue(
    options?.rules ?? [structuredClone(ruleFixture)]
  )
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
}

function mountPage(): VueWrapper {
  return mount(ZBanRules)
}

beforeEach(() => {
  vi.mocked(getRules).mockReset()
  vi.mocked(getBannedUsers).mockReset()
  vi.mocked(updateRule).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
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
  expect(wrapper.text()).toContain('同学号多设备登录')
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

  expect(updateRule).toHaveBeenCalledWith('multi_device_login', { enabled: false })
  expect(wrapper.find('.el-switch-stub').attributes('data-checked')).toBe('false')

  wrapper.unmount()
})

it('rolls the switch back and shows an error when the update fails', async () => {
  mockApi({ updateRejects: true })
  const wrapper = mountPage()
  await flushPromises()

  await wrapper.find('.el-switch-stub').trigger('click')
  await flushPromises()

  expect(updateRule).toHaveBeenCalledWith('multi_device_login', { enabled: false })
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
    'manage.banRules.dialog.title {"name":"同学号多设备登录"}'
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

  expect(updateRule).toHaveBeenCalledWith('multi_device_login', {
    config: { threshold: 4, window_hours: 48 },
    penalties: [2, 5]
  })
  expect(elMessageSuccessSpy).toHaveBeenCalledWith('manage.banRules.savedNotice')
  expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)

  wrapper.unmount()
})

it('renders banned users with pagination and re-queries on page change', async () => {
  mockApi({ usersSize: 25 })
  const wrapper = mountPage()
  await flushPromises()

  expect(getBannedUsers).toHaveBeenCalledWith(1, 20, '')
  expect(wrapper.text()).toContain('张三')
  expect(wrapper.text()).toContain('高三8班')
  expect(wrapper.text()).toContain('manage.banRules.users.permanent')

  const pagination = wrapper.find('.z-pagination-stub')
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

  const searchInput = wrapper.find('.el-input-stub')
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

  const empties = wrapper.findAll('.el-empty-stub')
  expect(empties.length).toBe(1)
  expect(empties[0]!.text()).toBe('manage.banRules.users.empty')
  expect(wrapper.text()).not.toContain('张三')

  wrapper.unmount()
})

it('shows empty states when both endpoints fail (e.g. 403)', async () => {
  vi.mocked(getRules).mockRejectedValue(new Error('forbidden'))
  vi.mocked(getBannedUsers).mockRejectedValue(new Error('forbidden'))
  const wrapper = mountPage()
  await flushPromises()

  const empties = wrapper.findAll('.el-empty-stub')
  expect(empties.length).toBe(2)
  expect(empties.every((node) => node.text() === 'manage.banRules.loadFailed')).toBe(true)

  wrapper.unmount()
})
