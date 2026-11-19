import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiLogsAdmin from './ZAiLogsAdmin.vue'
import { getAiLogStats, listAiLogs } from '@/api/ai'
import type { AiLogRecord, AiLogStats } from '@/../types/ai'

const { elMessageErrorSpy, elMessageSuccessSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listAiLogs: vi.fn(),
    getAiLogStats: vi.fn()
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
vi.mock('element-plus', async () => {  const { defineComponent, Fragment, h, ref } = await import('vue')

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
      modelValue: { type: [Array, Date, String, Number], default: null },
      type: String,
      placeholder: String
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-date-picker-stub',
          value: Array.isArray(props.modelValue)
            ? (props.modelValue as Date[]).map((item) => item.toISOString()).join(',')
            : '',
          placeholder: props.placeholder,
          onInput: (event: Event) => {
            const raw = (event.target as HTMLInputElement).value
            if (!raw) {
              emit('update:modelValue', null)
              return
            }
            emit('update:modelValue', [
              new Date('2026-10-01T00:00:00Z'),
              new Date('2026-10-24T00:00:00Z')
            ])
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
    setup(_, { slots, attrs }) {
      return () => h('span', { class: 'el-tag-stub', ...attrs }, slots.default?.())
    }
  })

  const ElPagination = defineComponent({
    name: 'ElPagination',
    props: {
      currentPage: { type: Number, default: 1 },
      pageSize: { type: Number, default: 10 },
      total: { type: Number, default: 0 }
    },
    emits: ['current-change', 'size-change'],
    setup(props, { emit }) {
      return () =>
        h(
          'div',
          {
            class: 'el-pagination-stub',
            'data-total': String(props.total),
            'data-page': String(props.currentPage),
            'data-size': String(props.pageSize)
          },
          [
            h(
              'button',
              {
                class: 'el-pagination-stub__next',
                onClick: () => emit('current-change', (props.currentPage ?? 1) + 1)
              },
              'next'
            )
          ]
        )
    }
  })

  return {
    ElCard: { name: 'ElCard', template: '<section><slot name="header" /><slot /></section>' },
    ElTable,
    ElTableColumn,
    ElButton,
    ElSelect,
    ElInput,
    ElOption,
    ElDatePicker,
    ElIcon,
    ElEmpty,
    ElTag,
    ElPagination,
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
      emits: ['update:currentPage', 'update:pageSize', 'change'],
      setup(props, { emit }) {
        return () =>
          h(
            'div',
            {
              class: 'z-pagination-stub',
              'data-total': String(props.total),
              'data-page': String(props.currentPage),
              'data-size': String(props.pageSize)
            },
            [
              h(
                'button',
                {
                  class: 'z-pagination-stub__next',
                  onClick: () => {
                    const next = props.currentPage + 1
                    emit('update:currentPage', next)
                    emit('change', next, props.pageSize)
                  }
                },
                'next'
              )
            ]
          )
      }
    })
  }
})

const ocrRow: AiLogRecord = {
  oid: 'oid-1',
  user_oid: 'uid-1',
  user_name: '张三',
  capability: 'ocr',
  endpoint: '/ai/ocr',
  provider: 'baidu',
  model: 'ocr-general',
  status: 'success',
  http_status: 200,
  error_code: null,
  latency_ms: 812,
  units: 1,
  input_bytes: 20480,
  request_id: 'req-0001',
  key_scope: 'shared',
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
  attempt_no: 1,
  purpose: 'ocr',
  first_token_ms: null,
  engine_kind: 'baidu',
  attachment_oid: 'att-1',
  roles_snapshot: 'admin,department',
  created_at: '2026-10-24T08:00:00'
}

const chatRow: AiLogRecord = {
  oid: 'oid-2',
  user_oid: 'uid-2',
  user_name: '李四',
  capability: 'chat',
  endpoint: '/ai/v1/chat/completions',
  provider: null,
  model: null,
  status: 'timeout',
  http_status: 504,
  error_code: 'AI_TIMEOUT',
  latency_ms: null,
  units: 3,
  input_bytes: null,
  request_id: null,
  key_scope: 'personal',
  prompt_tokens: 120,
  completion_tokens: 30,
  total_tokens: 150,
  attempt_no: 2,
  purpose: 'markdown-assistant',
  first_token_ms: 240,
  engine_kind: null,
  attachment_oid: null,
  roles_snapshot: 'department',
  created_at: '2026-10-24T09:00:00'
}

const statsFixture: AiLogStats = {
  days: 7,
  since: '2026-10-18 00:00:00',
  daily: [
    {
      date: '2026-10-24',
      calls: 4,
      errors: 1,
      prompt_tokens: 120,
      completion_tokens: 30,
      total_tokens: 150,
      avg_latency_ms: 200
    }
  ],
  by_provider: [
    { provider: 'baidu', calls: 3, errors: 0, units: 3, total_tokens: 0 },
    { provider: null, calls: 1, errors: 1, units: 3, total_tokens: 150 }
  ],
  by_model: [{ model: 'ocr-general', calls: 3, errors: 0, units: 3, total_tokens: 0 }],
  by_purpose: [
    { purpose: 'ocr', calls: 3, errors: 0, units: 3, total_tokens: 0 },
    { purpose: null, calls: 1, errors: 1, units: 3, total_tokens: 150 }
  ],
  by_user: [
    {
      user_oid: 'uid-1',
      name: '张三',
      calls: 3,
      errors: 0,
      total_tokens: 0,
      units: 3,
      avg_latency_ms: 200
    },
    {
      user_oid: 'uid-2',
      name: 'uid-2',
      calls: 1,
      errors: 1,
      total_tokens: 150,
      units: 3,
      avg_latency_ms: null
    }
  ],
  by_status: [
    { status: 'success', calls: 3 },
    { status: 'timeout', calls: 1 }
  ]
}

function mountPage(): VueWrapper {
  return mount(ZAiLogsAdmin)
}

function lastQuery() {
  const calls = vi.mocked(listAiLogs).mock.calls
  return calls[calls.length - 1]![0]
}

beforeEach(() => {
  vi.mocked(listAiLogs).mockReset()
  vi.mocked(getAiLogStats).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  vi.mocked(listAiLogs).mockResolvedValue({ rows: [ocrRow, chatRow], total: 42 })
  vi.mocked(getAiLogStats).mockResolvedValue(statsFixture)
})

describe('调用日志页', () => {
  it('渲染调用记录与关键列', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.find('.el-table-stub')
    expect(table.text()).toContain('张三')
    expect(table.text()).toContain('李四')
    expect(table.text()).toContain('/ai/v1/chat/completions')
    expect(table.text()).toContain('ocr-general')
    expect(table.text()).toContain('AI_TIMEOUT')
    expect(table.text()).toContain('812')
    expect(table.text()).toContain('req-0001')

    wrapper.unmount()
  })

  it('新增列：场景、引擎类型、token 三段、首字耗时与尝试序号', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.find('.el-table-stub')
    // purpose / engine_kind
    expect(table.text()).toContain('markdown-assistant')
    expect(table.text()).toContain('baidu')
    // token「入 / 出 / 合计」；没有 usage 的行显示占位符而不是 0
    expect(table.text()).toContain('120 / 30 / 150')
    // first_token_ms 与 attempt_no
    expect(table.text()).toContain('240 ai.logs.latencyUnit')
    expect(table.text()).toContain('ai.logs.columns.tokens')

    wrapper.unmount()
  })

  it('token 为 null 的行显示占位符：那是「上游没报」，不是 0', async () => {
    vi.mocked(listAiLogs).mockResolvedValue({ rows: [ocrRow], total: 1 })
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.find('.el-table-stub')
    const tokensCell = table.find('tbody tr').findAll('td')[13]!
    expect(tokensCell.text()).toBe('—')

    wrapper.unmount()
  })

  it('总数来自 metadata.size 并交给分页组件', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const pagination = wrapper.find('.z-pagination-stub')
    expect(pagination.attributes('data-total')).toBe('42')

    wrapper.unmount()
  })

  it('状态标签按五种状态命中配色标记', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('[data-status="success"]').text()).toBe('ai.logs.statusSuccess')
    expect(wrapper.find('[data-status="timeout"]').text()).toBe('ai.logs.statusTimeout')

    wrapper.unmount()
  })

  it('角色快照含 admin 的用户旁挂「管理员」标识，非管理员不挂', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const rows = wrapper.findAll('.el-table-stub__row')
    expect(rows[0]!.find('.ai-log-user__admin').exists()).toBe(true)
    expect(rows[0]!.find('.ai-log-user__admin').text()).toBe('ai.logs.columns.adminBadge')
    expect(rows[1]!.find('.ai-log-user__admin').exists()).toBe(false)

    wrapper.unmount()
  })

  it('历史 warning 行（管理员旁路）：状态列翻成「管理员旁路」而不是原样英文', async () => {
    vi.mocked(listAiLogs).mockResolvedValue({
      rows: [
        {
          ...ocrRow,
          oid: 'oid-3',
          status: 'warning',
          error_code: 'AI_AGENT_MODERATION_SKIPPED',
          roles_snapshot: 'admin'
        }
      ],
      total: 1
    })
    const wrapper = mountPage()
    await flushPromises()

    const tag = wrapper.find('[data-status="warning"]')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toBe('ai.logs.statusAdminBypass')
    // 管理员标识与历史状态可以同时出现：旁路就是管理员做的。
    expect(wrapper.find('.ai-log-user__admin').exists()).toBe(true)

    wrapper.unmount()
  })

  it('翻页时把页码与每页条数传给接口', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.z-pagination-stub__next').trigger('click')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 2, perpage: 20 })

    wrapper.unmount()
  })

  it('过滤条件变化后回到第一页并带上过滤值', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.z-pagination-stub__next').trigger('click')
    await flushPromises()
    expect(lastQuery()).toMatchObject({ page: 2 })

    const selects = wrapper.findAll('.el-select-stub')
    await selects[0]!.setValue('ocr')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 1, capability: 'ocr' })

    wrapper.unmount()
  })

  it('时间范围折成 epoch 秒传给接口', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-date-picker-stub').setValue('range')
    await flushPromises()

    expect(lastQuery()).toMatchObject({
      time_start: Math.floor(new Date('2026-10-01T00:00:00Z').getTime() / 1000),
      time_end: Math.floor(new Date('2026-10-24T00:00:00Z').getTime() / 1000)
    })

    wrapper.unmount()
  })

  it('加载失败时给出空态提示', async () => {
    vi.mocked(listAiLogs).mockRejectedValue(new Error('forbidden'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.el-empty-stub').text()).toBe('ai.logs.loadFailed')

    wrapper.unmount()
  })

  it('新增过滤条件（场景/服务商/模型/请求编号）都传给接口', async () => {
    const wrapper = mountPage()
    await flushPromises()

    // 场景是过滤器里的第 4 个下拉（能力、状态、来源之后；统计窗口那个在它后面）。
    const selects = wrapper.findAll('.el-select-stub')
    await selects[3]!.setValue('markdown-assistant')
    await flushPromises()
    expect(lastQuery()).toMatchObject({ purpose: 'markdown-assistant' })

    const inputs = wrapper.findAll('.el-input-stub')
    await inputs[0]!.setValue('team')
    await inputs[1]!.setValue('gpt-x')
    await inputs[2]!.setValue('req-0001')
    await flushPromises()

    expect(lastQuery()).toMatchObject({
      provider: 'team',
      model: 'gpt-x',
      request_id: 'req-0001'
    })

    wrapper.unmount()
  })

  it('统计概览：四个总数由 daily 累加，并渲染分组小计', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(getAiLogStats).toHaveBeenCalledWith(7)
    const stats = wrapper.find('.ai-log-stats__tile').element.parentElement!
    expect(stats.textContent).toContain('4')
    expect(stats.textContent).toContain('150')
    // 失败率 1/4 → 25%
    expect(stats.textContent).toContain('25%')
    // 分组小计：服务商与场景各一张表
    expect(wrapper.text()).toContain('ai.logs.stats.byProvider')
    expect(wrapper.text()).toContain('ai.logs.stats.byPurpose')

    wrapper.unmount()
  })

  it('统计窗口可切换：切换后按新天数重新拉取', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const selects = wrapper.findAll('.el-select-stub')
    await selects[selects.length - 1]!.setValue('30')
    await flushPromises()

    expect(getAiLogStats).toHaveBeenLastCalledWith('30')

    wrapper.unmount()
  })

  it('统计数据加载失败不影响日志列表', async () => {
    vi.mocked(getAiLogStats).mockRejectedValue(new Error('boom'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('ai.logs.stats.refreshFailed')
    expect(wrapper.find('.el-table-stub').text()).toContain('张三')
    expect(elMessageErrorSpy).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('统计概览：按用户表渲染显示名、token、额度与平均延迟', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('ai.logs.stats.byUser')

    // 概览里第 4 张表是按用户（前 3 张：按日、按服务商、按场景）。
    const byUser = wrapper.findAll('.ai-log-stats__grid')[3]!
    expect(byUser.text()).toContain('张三')
    expect(byUser.text()).toContain('uid-1')
    // 名字查不到时后端回落到 oid，前端原样显示而不是盖成「未知用户」
    expect(byUser.text()).toContain('uid-2')
    expect(byUser.text()).toContain('150')
    expect(byUser.text()).toContain('200 ai.logs.latencyUnit')

    // 没有延迟观测的行给占位符，不是 0
    const secondRow = byUser.findAll('tbody tr')[1]!
    expect(secondRow.findAll('td')[5]!.text()).toBe('—')

    wrapper.unmount()
  })

  it('统计概览：按状态表把状态翻成中文并给出调用数', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('ai.logs.stats.byStatus')

    const byStatus = wrapper.findAll('.ai-log-stats__grid')[4]!
    expect(byStatus.text()).toContain('ai.logs.statusSuccess')
    expect(byStatus.text()).toContain('ai.logs.statusTimeout')

    const statusRows = byStatus.findAll('tbody tr')
    expect(statusRows).toHaveLength(2)
    expect(statusRows[0]!.findAll('td')[1]!.text()).toBe('3')
    expect(statusRows[1]!.findAll('td')[1]!.text()).toBe('1')

    wrapper.unmount()
  })

  it('统计响应里两张新表为空时走空态文案，不影响其余小计', async () => {
    vi.mocked(getAiLogStats).mockResolvedValue({
      ...statsFixture,
      by_user: [],
      by_status: []
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('ai.logs.stats.byUser')
    expect(wrapper.text()).toContain('ai.logs.stats.byStatus')
    expect(wrapper.text().split('ai.logs.stats.noData').length - 1).toBe(2)
    expect(wrapper.text()).toContain('ai.logs.stats.byProvider')

    wrapper.unmount()
  })
})
