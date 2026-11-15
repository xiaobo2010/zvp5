import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiLogsAdmin from './ZAiLogsAdmin.vue'
import { listAiLogs } from '@/api/ai'
import type { AiLogRecord } from '@/../types/ai'

const { elMessageErrorSpy, elMessageSuccessSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listAiLogs: vi.fn()
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

  return {
    ElCard: { name: 'ElCard', template: '<section><slot name="header" /><slot /></section>' },
    ElTable,
    ElTableColumn,
    ElButton,
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
  created_at: '2026-10-24T09:00:00'
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
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  vi.mocked(listAiLogs).mockResolvedValue({ rows: [ocrRow, chatRow], total: 42 })
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

  it('总数来自 metadata.size 并交给分页组件', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const pagination = wrapper.find('.yg-pg')
    expect(pagination.exists()).toBe(true)
    // 42 条 / 每页 20 条 ⇒ 3 页
    expect(pagination.find('.yg-pg__total').text()).toContain('42')
    expect(pagination.find('.yg-pg__status').text()).toContain('3')

    wrapper.unmount()
  })

  it('状态标签按五种状态命中配色标记', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('[data-status="success"]').text()).toBe('ai.logs.statusSuccess')
    expect(wrapper.find('[data-status="timeout"]').text()).toBe('ai.logs.statusTimeout')

    wrapper.unmount()
  })

  it('翻页时把页码与每页条数传给接口', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('[aria-label="下一页"]').trigger('click')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 2, perpage: 20 })

    wrapper.unmount()
  })

  it('过滤条件变化后回到第一页并带上过滤值', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('[aria-label="下一页"]').trigger('click')
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
})
