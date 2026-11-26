import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { h } from 'vue'
import ManageRoles from './ManageRoles.vue'
import type { RolePoliciesData, RoleQuotas } from '@/api/system/rolePolicies'

/**
 * 角色管理页的两块内容：
 * - 角色策略：功能开关矩阵 + 月度配额，保存走双人授权（TOTP）；
 * - 用户角色：原有表格逻辑，搬进二级导航后行为不变。
 *
 * 请求层不 mock：`@/plugins/axios` 才是替身，角色策略 API 文件按真实路径跑一遍，
 * envelope 拆包与 AiRequestError 映射都在链路里，页面拿到的错误形状与线上一致。
 */

interface AxiosCall {
  method: string
  url: string
  data?: Record<string, unknown>
}

const {
  axiosMock,
  openSpy,
  succeedSpy,
  failSpy,
  readUsersSpy,
  updateRolesSpy,
  elMessageErrorSpy,
  elMessageSuccessSpy,
  elMessageBoxConfirmSpy,
  storeState
} = vi.hoisted(() => ({
  axiosMock: vi.fn<(config: AxiosCall) => Promise<unknown>>(),
  openSpy: vi.fn<(scene: string) => Promise<string | null>>(),
  succeedSpy: vi.fn(),
  failSpy: vi.fn(),
  readUsersSpy: vi.fn(),
  updateRolesSpy: vi.fn(),
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  elMessageBoxConfirmSpy: vi.fn(),
  storeState: { _id: 'me-oid', position: ['admin'] as string[] }
}))

vi.mock('@/plugins/axios', () => ({ default: axiosMock, baseURL: '' }))

vi.mock('@/api', () => ({
  default: {
    user: {
      read_v2: readUsersSpy,
      updateRoles: updateRolesSpy
    }
  }
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => storeState
}))

// 用真实中文 locale 当翻译器：断言里写「没有权限修改角色策略」这种文案，
// 才能证明 403/400 落到人话提示，而不是仅仅把 key 传了出去。
vi.mock('vue-i18n', async () => {
  const { zhCN } = await import('@/i18n/locales/manage')
  const messages: Record<string, unknown> = { manage: zhCN }

  function lookup(path: string): unknown {
    return path.split('.').reduce<unknown>((node, part) => {
      if (node && typeof node === 'object') {
        return (node as Record<string, unknown>)[part]
      }
      return undefined
    }, messages)
  }

  function translate(key: string, params?: Record<string, unknown>): string {
    const value = lookup(key)
    if (typeof value !== 'string') {
      return key
    }
    if (!params) {
      return value
    }
    return value.replace(/\{(\w+)\}/g, (matched, name: string) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : matched
    )
  }

  return { useI18n: () => ({ t: translate }) }
})

vi.mock('@/components/system/ZSystemAuthDialog.vue', () => ({
  default: {
    name: 'ZSystemAuthDialogStub',
    setup(_props: unknown, { expose }: { expose: (api: Record<string, unknown>) => void }) {
      expose({ open: openSpy, succeed: succeedSpy, fail: failSpy })
      return () => h('div', { class: 'auth-dialog-stub' })
    }
  }
}))

vi.mock('@/components/utils/ZPagination.vue', () => ({
  default: {
    name: 'ZPaginationStub',
    setup() {
      return () => h('div', { class: 'pagination-stub' })
    }
  }
}))

vi.mock('@/components/tags/ZUserGroup.vue', () => ({
  default: {
    name: 'ZUserGroupStub',
    props: { group: { type: String, default: '' } },
    setup() {
      return () => h('span', { class: 'group-stub' })
    }
  }
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { computed, defineComponent, h: render, inject, provide } = await import('vue')

  const ElButton = defineComponent({
    name: 'ElButton',
    props: {
      type: String,
      size: String,
      loading: Boolean,
      disabled: Boolean,
      text: Boolean,
      bg: Boolean,
      icon: { type: [Object, String, Function], default: undefined }
    },
    emits: ['click'],
    setup(props, { emit, slots, attrs }) {
      return () =>
        render(
          'button',
          {
            ...attrs,
            class: ['el-button-stub', attrs.class],
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

  const ElSwitch = defineComponent({
    name: 'ElSwitch',
    props: { modelValue: { type: Boolean, default: false }, disabled: Boolean },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        render('button', {
          class: 'el-switch-stub',
          'data-checked': props.modelValue ? 'true' : 'false',
          disabled: props.disabled || undefined,
          onClick: () => {
            if (!props.disabled) {
              emit('update:modelValue', !props.modelValue)
            }
          }
        })
    }
  })

  const ElInputNumber = defineComponent({
    name: 'ElInputNumber',
    props: {
      modelValue: { type: Number, default: undefined },
      disabled: Boolean,
      min: Number,
      step: Number,
      controls: Boolean,
      placeholder: String
    },
    emits: ['update:modelValue'],
    setup(props, { emit, attrs }) {
      return () =>
        render('input', {
          ...attrs,
          class: ['el-input-number-stub', attrs.class],
          value: props.modelValue ?? '',
          placeholder: props.placeholder,
          disabled: props.disabled || undefined,
          onInput: (event: Event) => {
            const raw = (event.target as HTMLInputElement).value.trim()
            emit('update:modelValue', raw === '' ? undefined : Number(raw))
          }
        })
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: String, default: '' }, size: String, clearable: Boolean },
    emits: ['update:modelValue'],
    setup(props, { emit, attrs }) {
      return () =>
        render('input', {
          ...attrs,
          class: ['el-input-stub', attrs.class],
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElCard = defineComponent({
    name: 'ElCard',
    setup(_, { slots, attrs }) {
      return () =>
        render('section', { ...attrs, class: ['el-card-stub', attrs.class] }, [
          slots.header?.(),
          slots.default?.()
        ])
    }
  })

  const ElTable = defineComponent({
    name: 'ElTable',
    props: { data: { type: Array, default: () => [] }, maxHeight: Number },
    setup(props, { slots, attrs }) {
      // 行的数据源交给列自行取用（真实 ElTable 也是父级持有 data、列只负责取 row）
      provide('elTableRows', () => props.data as Array<Record<string, unknown>>)
      return () =>
        render('div', { ...attrs, class: ['el-table-stub', attrs.class] }, slots.default?.())
    }
  })

  const ElTableColumn = defineComponent({
    name: 'ElTableColumn',
    props: {
      label: String,
      prop: String,
      width: [Number, String],
      minWidth: [Number, String],
      fixed: String,
      showOverflowTooltip: Boolean
    },
    setup(props, { slots }) {
      const rows = inject<() => Array<Record<string, unknown>>>('elTableRows', () => [])
      return () =>
        render(
          'div',
          { class: 'el-table-column-stub', 'data-column': props.label },
          rows().map((row) =>
            render(
              'div',
              { class: 'el-table-cell-stub' },
              slots.default
                ? slots.default({ row })
                : String(row[props.prop ?? ''] ?? '')
            )
          )
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
          ? render('div', { class: 'el-dialog-stub' }, [slots.default?.(), slots.footer?.()])
          : null
    }
  })

  const ElCheckboxGroup = defineComponent({
    name: 'ElCheckboxGroup',
    props: { modelValue: { type: Array, default: () => [] }, disabled: Boolean },
    emits: ['update:modelValue'],
    setup(props, { emit, slots }) {
      const selected = computed(() => (props.modelValue ?? []) as Array<string | number>)
      const toggle = (value: string | number) => {
        const next = [...selected.value]
        const index = next.indexOf(value)
        if (index >= 0) {
          next.splice(index, 1)
        } else {
          next.push(value)
        }
        emit('update:modelValue', next)
      }
      provide('elCheckboxGroup', { selected, toggle })
      return () => render('div', { class: 'el-checkbox-group-stub' }, slots.default?.())
    }
  })

  const ElCheckbox = defineComponent({
    name: 'ElCheckbox',
    props: {
      modelValue: Boolean,
      value: { type: [String, Number], default: undefined },
      disabled: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { emit, slots }) {
      const group = inject<{
        selected: { value: Array<string | number> }
        toggle: (value: string | number) => void
      } | null>('elCheckboxGroup', null)
      const isChecked = () =>
        group && props.value !== undefined
          ? group.selected.value.includes(props.value)
          : props.modelValue
      return () =>
        render('label', { class: 'el-checkbox-stub' }, [
          render('input', {
            type: 'checkbox',
            'data-value': props.value,
            checked: isChecked(),
            disabled: props.disabled || undefined,
            onChange: () => {
              if (group && props.value !== undefined) {
                group.toggle(props.value)
              } else if (!props.disabled) {
                emit('update:modelValue', !props.modelValue)
              }
            }
          }),
          slots.default?.()
        ])
    }
  })

  const passthrough = (name: string, tag = 'span') =>
    defineComponent({
      name,
      setup(_, { slots, attrs }) {
        return () => render(tag, { ...attrs }, slots.default?.())
      }
    })

  return {
    ElButton,
    ElSwitch,
    ElInputNumber,
    ElInput,
    ElCard,
    ElTable,
    ElDialog,
    ElTableColumn,
    ElCheckbox,
    ElCheckboxGroup,
    ElForm: passthrough('ElForm', 'form'),
    ElFormItem: passthrough('ElFormItem', 'div'),
    ElTag: passthrough('ElTag'),
    ElTooltip: passthrough('ElTooltip'),
    ElIcon: passthrough('ElIcon', 'i'),
    ElOption: passthrough('ElOption'),
    ElSelect: passthrough('ElSelect', 'div'),
    ElMessage: Object.assign(vi.fn(), {
      success: elMessageSuccessSpy,
      error: elMessageErrorSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: { confirm: elMessageBoxConfirmSpy },
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {}, unmounted() {} }
  }
})

const FEATURE_KEYS = [
  'ai.chat',
  'ai.ocr',
  'ai.agent',
  'ai.schedule',
  'ai.keys',
  'activity.stats',
  'attachment.manage',
  'export.time',
  'import.activity',
  'logs.read',
  'notify.publish',
  'ban.read',
  'quota.manage'
]

const ALL_ON: Record<string, boolean> = Object.fromEntries(FEATURE_KEYS.map((key) => [key, true]))

const POLICIES: RolePoliciesData = {
  roles: [
    {
      role: 'secretary',
      features: { ...ALL_ON },
      quotas: { af: 300, schedule: 1 },
      updated_at: '2026-10-01T09:30:00',
      updated_by: 'oid-admin'
    },
    {
      role: 'department',
      features: { ...ALL_ON, 'ai.agent': false },
      quotas: { af: 600, schedule: 1 },
      updated_at: null,
      updated_by: null
    },
    {
      role: 'student',
      features: { ...ALL_ON, 'ai.chat': false, 'ai.agent': false },
      quotas: { af: null, schedule: 0 },
      updated_at: null,
      updated_by: null
    },
    {
      role: 'admin',
      features: { ...ALL_ON },
      quotas: { af: null, schedule: null },
      updated_at: null,
      updated_by: null
    }
  ],
  editable_roles: ['secretary', 'department', 'student'],
  feature_keys: FEATURE_KEYS
}

function okResponse(data: unknown) {
  return { status: 200, data: { status: 'ok', code: 200, data }, headers: {} }
}

function failResponse(status: number, detail: unknown) {
  return { status, data: { status: 'error', code: status, detail }, headers: {} }
}

function mountPage(): VueWrapper {
  return mount(ManageRoles)
}

function switchOf(wrapper: VueWrapper, key: string) {
  return wrapper.find(`.policy-row[data-feature="${key}"] .el-switch-stub`)
}

function quotaOf(wrapper: VueWrapper, kind: string) {
  return wrapper.find(`.policy-row[data-quota="${kind}"] .el-input-number-stub`)
}

function putCalls(): AxiosCall[] {
  return axiosMock.mock.calls.map((call) => call[0]).filter((call) => call.method === 'put')
}

function getCalls(): AxiosCall[] {
  return axiosMock.mock.calls.map((call) => call[0]).filter((call) => call.method === 'get')
}

beforeEach(() => {
  axiosMock.mockReset()
  openSpy.mockReset()
  succeedSpy.mockReset()
  failSpy.mockReset()
  readUsersSpy.mockReset()
  updateRolesSpy.mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()

  storeState.position = ['admin']
  openSpy.mockResolvedValue('123456')
  elMessageBoxConfirmSpy.mockResolvedValue('confirm')
  readUsersSpy.mockResolvedValue({ users: [], size: 0 })

  axiosMock.mockImplementation((config: AxiosCall) => {
    if (config.method === 'get' && config.url === '/system/role-policies') {
      return Promise.resolve(okResponse(POLICIES))
    }
    if (config.method === 'put') {
      const role = config.url.split('/').pop() ?? ''
      const body = config.data as unknown as {
        features: Record<string, boolean>
        quotas: RoleQuotas
      }
      return Promise.resolve(
        okResponse({
          role,
          features: body.features,
          quotas: body.quotas,
          updated_at: '2026-10-28T10:00:00',
          updated_by: 'oid-admin'
        })
      )
    }
    return Promise.resolve(okResponse(null))
  })
})

describe('角色策略', () => {
  it('加载中先显示占位文案，成功后换成开关矩阵', async () => {
    const wrapper = mountPage()

    // 还没等请求落地：此时只有加载卡片
    expect(wrapper.text()).toContain('策略加载中…')
    expect(wrapper.findAll('.el-switch-stub')).toHaveLength(0)

    await flushPromises()
    expect(wrapper.text()).not.toContain('策略加载中…')
    expect(wrapper.findAll('.el-switch-stub')).toHaveLength(13)
  })

  it('渲染 13 个功能开关与 3 个配额输入', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('.el-switch-stub')).toHaveLength(13)
    expect(wrapper.findAll('.el-input-number-stub')).toHaveLength(2)
    expect(wrapper.find('[data-role="secretary"]').exists()).toBe(true)
    expect(wrapper.find('[data-role="department"]').exists()).toBe(true)
    expect(wrapper.find('[data-role="student"]').exists()).toBe(true)
  })

  it('功能开关按 AI 能力 / 活动与数据 / 运维只读 三组渲染', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('AI 能力')
    expect(wrapper.text()).toContain('活动与数据')
    expect(wrapper.text()).toContain('运维只读')
    expect(wrapper.text()).toContain('LLM 对话')
    expect(wrapper.text()).toContain('允许在 ZVAgent 里发起多轮对话')
    expect(wrapper.text()).toContain('每月统一额度：对话与 OCR 记在同一个池子')
    expect(wrapper.text()).toContain('当前规则：学生会 1 条；0 表示不允许创建')
  })

  it('切换角色时弹未保存提示，且各角色的草稿互不覆盖', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await switchOf(wrapper, 'ai.chat').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('有未保存的改动')

    await wrapper.find('[data-role="department"]').trigger('click')
    await flushPromises()
    expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
    expect(elMessageBoxConfirmSpy.mock.calls[0]?.[0]).toContain('有未保存的改动')
    // 学生会的草稿是干净的：agent 开关仍是后端下发的 false
    expect(switchOf(wrapper, 'ai.agent').attributes('data-checked')).toBe('false')

    // 回到团支书：学生会没有改动，不该再弹一次；草稿也还在
    await wrapper.find('[data-role="secretary"]').trigger('click')
    await flushPromises()
    expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
    expect(switchOf(wrapper, 'ai.chat').attributes('data-checked')).toBe('false')
  })

  it('取消切换时留在原角色，草稿不丢', async () => {
    elMessageBoxConfirmSpy.mockRejectedValueOnce(new Error('cancel'))
    const wrapper = mountPage()
    await flushPromises()

    await switchOf(wrapper, 'ai.chat').trigger('click')
    await flushPromises()

    await wrapper.find('[data-role="department"]').trigger('click')
    await flushPromises()

    expect(elMessageBoxConfirmSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-role="secretary"]').classes()).toContain('is-active')
    expect(wrapper.find('[data-role="department"]').classes()).not.toContain('is-active')
    expect(switchOf(wrapper, 'ai.chat').attributes('data-checked')).toBe('false')
  })

  it('TOTP 弹层被取消时不发 PUT', async () => {
    openSpy.mockResolvedValueOnce(null)
    const wrapper = mountPage()
    await flushPromises()

    await switchOf(wrapper, 'ai.chat').trigger('click')
    await flushPromises()

    await wrapper.find('.policy-save').trigger('click')
    await flushPromises()

    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(putCalls()).toHaveLength(0)
    expect(elMessageSuccessSpy).not.toHaveBeenCalled()
  })

  it('确认动态码后 PUT 全量 features/quotas，成功回填并提示', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await switchOf(wrapper, 'ai.chat').trigger('click')
    await flushPromises()
    await quotaOf(wrapper, 'af').setValue('800')
    await flushPromises()

    await wrapper.find('.policy-save').trigger('click')
    await flushPromises()

    expect(openSpy.mock.calls[0]?.[0]).toContain('保存「团支书」的角色策略')
    const calls = putCalls()
    expect(calls).toHaveLength(1)
    expect(calls[0]?.url).toBe('/system/role-policies/secretary')
    const body = calls[0]?.data as {
      features: Record<string, boolean>
      quotas: RoleQuotas
      totp_code: string
    }
    expect(body.totp_code).toBe('123456')
    expect(Object.keys(body.features)).toHaveLength(13)
    expect(body.features['ai.chat']).toBe(false)
    expect(body.quotas).toEqual({ af: 800, schedule: 1 })

    expect(succeedSpy).toHaveBeenCalled()
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('角色策略已保存')
    // 回填后不再有未保存标记
    expect(wrapper.text()).not.toContain('有未保存的改动')
    expect(quotaOf(wrapper, 'af').attributes('value')).toBe('800')
  })

  it('保存失败时把后端 detail 交给授权弹层，不弹成功提示', async () => {
    axiosMock.mockImplementation((config: AxiosCall) => {
      if (config.method === 'get') {
        return Promise.resolve(okResponse(POLICIES))
      }
      return Promise.resolve(
        failResponse(400, {
          code: 'ROLE_POLICY_INVALID',
          message: '学生角色最多只能开启 3 个开关'
        })
      )
    })
    const wrapper = mountPage()
    await flushPromises()

    await switchOf(wrapper, 'ai.chat').trigger('click')
    await flushPromises()

    await wrapper.find('.policy-save').trigger('click')
    await flushPromises()

    expect(failSpy).toHaveBeenCalledWith('学生角色最多只能开启 3 个开关')
    expect(elMessageSuccessSpy).not.toHaveBeenCalled()
    // 弹层不关：用户可以直接补一个新动态码
    expect(succeedSpy).not.toHaveBeenCalled()
  })

  it('403 时给出中文权限说明，而不是空白页', async () => {
    axiosMock.mockImplementation(() => Promise.resolve(failResponse(403, 'Permission denied')))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('没有权限修改角色策略（仅管理员可用）')
    expect(wrapper.findAll('.el-switch-stub')).toHaveLength(0)
  })

  it('加载失败时展示错误并给出重试入口', async () => {
    axiosMock.mockImplementation(() => Promise.resolve(failResponse(500, 'backend down')))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.policy-state__error').exists()).toBe(true)
    expect(wrapper.text()).toContain('backend down')

    axiosMock.mockImplementation(() => Promise.resolve(okResponse(POLICIES)))
    await wrapper.find('.policy-state .el-button-stub').trigger('click')
    await flushPromises()

    expect(getCalls().length).toBeGreaterThanOrEqual(2)
    expect(wrapper.findAll('.el-switch-stub')).toHaveLength(13)
  })

  it('非 admin 不显示保存按钮', async () => {
    storeState.position = ['department']
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('.el-switch-stub')).toHaveLength(13)
    expect(wrapper.find('.policy-save').exists()).toBe(false)
    expect(wrapper.find('[data-role="department"]').exists()).toBe(true)
  })

  it('管理员角色只读，并提示固定全量权限', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('[data-role="admin"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('管理员固定全量权限')
    expect(switchOf(wrapper, 'ai.chat').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.policy-save').attributes('disabled')).toBeDefined()
  })
})

describe('用户角色', () => {
  it('切到「用户角色」仍是原来的筛选 + 表格', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('[data-tab="users"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.roles-search').exists()).toBe(true)
    expect(wrapper.find('.el-table-stub').exists()).toBe(true)
    expect(readUsersSpy).toHaveBeenCalledWith(1, 10, '', '')
  })

  it('渲染用户列表，并在弹窗里保存角色（原有回归）', async () => {
    readUsersSpy.mockResolvedValue({
      users: [
        {
          _id: 'u-1',
          id: 20240001,
          name: '张三',
          group: [],
          roles: ['secretary'],
          banned: false
        }
      ],
      size: 1
    })
    updateRolesSpy.mockResolvedValue(true)

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('[data-tab="users"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('张三')

    const editButton = wrapper
      .findAll('.el-table-stub .el-button-stub')
      .find((button) => button.text().includes('修改角色'))
    expect(editButton).toBeTruthy()
    await editButton?.trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)

    // 追加「学生会」：角色按勾选顺序回传
    await wrapper.find('.el-dialog-stub input[data-value="department"]').trigger('change')
    await flushPromises()

    const saveButton = wrapper
      .findAll('.el-dialog-stub .el-button-stub')
      .find((button) => button.text().includes('保存'))
    expect(saveButton).toBeTruthy()
    await saveButton?.trigger('click')
    await flushPromises()

    expect(updateRolesSpy).toHaveBeenCalledWith('u-1', ['secretary', 'department'])
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('角色已更新')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    // 保存后重新拉了一次列表
    expect(readUsersSpy.mock.calls.length).toBeGreaterThanOrEqual(2)
  })
})
