import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiPromptAudit from './ZAiPromptAudit.vue'
import { AiRequestError, getAiPrompt, listAiPrompts } from '@/api/ai'
import type { AiPromptAuditRecord } from '@/../types/ai'

const { elMessageErrorSpy, elMessageSuccessSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listAiPrompts: vi.fn(),
    getAiPrompt: vi.fn()
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
              'data-page': String(props.currentPage)
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

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, Fragment, h } = await import('vue')

  const ElTable = defineComponent({
    name: 'ElTable',
    props: { data: { type: Array, default: () => [] } },
    emits: ['row-click'],
    setup(props, { slots, emit }) {
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
                {
                  class: 'el-table-stub__row',
                  onClick: () => emit('row-click', row)
                },
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

  const ElDrawer = defineComponent({
    name: 'ElDrawer',
    props: {
      modelValue: Boolean,
      title: String,
      size: String,
      direction: String
    },
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('aside', { class: 'el-drawer-stub' }, [
              h('header', { class: 'el-drawer-stub__title' }, props.title),
              ...(slots.default?.() ?? [])
            ])
          : null
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

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: {
      modelValue: { type: [String, Number], default: '' },
      placeholder: String,
      disabled: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-select-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          disabled: props.disabled || undefined,
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

  const ElTooltip = defineComponent({
    name: 'ElTooltip',
    props: { content: String, disabled: Boolean, placement: String },
    setup(props, { slots }) {
      return () =>
        h(
          'div',
          {
            class: 'el-tooltip-stub',
            'data-content': props.content ?? '',
            'data-disabled': String(Boolean(props.disabled))
          },
          slots.default?.()
        )
    }
  })

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: [String, Number] },
    setup() {
      return () => null
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
    setup(props, { slots }) {
      return () =>
        h('div', { class: 'el-empty-stub' }, [props.description, slots.default?.()])
    }
  })

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String },
    setup(_, { slots, attrs }) {
      return () =>
        h('span', { class: 'el-tag-stub', ...attrs }, slots.default?.())
    }
  })

  return {
    ElTable,
    ElTableColumn,
    ElDrawer,
    ElButton,
    ElSelect,
    ElInput,
    ElDatePicker,
    ElTooltip,
    ElOption,
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

const recordFixture: AiPromptAuditRecord = {
  oid: 'oid-1',
  user_oid: 'uid-1',
  user_name: '张三',
  purpose: 'chat',
  roles_snapshot: 'admin,department',
  prompt_chars: 1280,
  created_at: '2026-10-24T08:00:00'
}

const detailFixture = {
  oid: 'oid-1',
  user_oid: 'uid-1',
  user_name: '张三',
  request_id: 'req-0001',
  purpose: 'chat',
  roles_snapshot: 'admin,department',
  prompt_text: '你好，帮我查一下本月活动。',
  answer_text: null,
  tool_calls: null,
  refused: 0,
  warned: 0,
  created_at: '2026-10-24T08:00:00'
}

const RANGE_START_EPOCH = Math.floor(new Date('2026-10-01T00:00:00Z').getTime() / 1000)
const RANGE_END_EPOCH = Math.floor(new Date('2026-10-24T00:00:00Z').getTime() / 1000)

function mountPage(): VueWrapper {
  return mount(ZAiPromptAudit)
}

function lastQuery() {
  const calls = vi.mocked(listAiPrompts).mock.calls
  return calls[calls.length - 1]![0]
}

/** 天数是第二个 ElSelect（第一个是场景）。 */
function daysSelect(wrapper: VueWrapper) {
  return wrapper.findAll('.el-select-stub')[1]!
}

function purposeSelect(wrapper: VueWrapper) {
  return wrapper.findAll('.el-select-stub')[0]!
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('.el-button-stub').find((item) => item.text() === text)!
}

beforeEach(() => {
  vi.mocked(listAiPrompts).mockReset()
  vi.mocked(getAiPrompt).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  vi.mocked(listAiPrompts).mockResolvedValue({ rows: [recordFixture], total: 42 })
  vi.mocked(getAiPrompt).mockResolvedValue(detailFixture)
})

describe('Prompt 审阅页', () => {
  it('列表渲染元数据列：时间、用户、角色快照、场景与字符数', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.find('.el-table-stub')
    expect(table.text()).toContain('2026-10-24 08:00')
    expect(table.text()).toContain('张三')
    expect(table.text()).toContain('uid-1')
    expect(table.text()).toContain('admin,department')
    expect(table.text()).toContain('chat')
    expect(table.text()).toContain('1280')

    wrapper.unmount()
  })

  it('标题让人知道这里能查 Prompt', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.yg-card__title').text()).toBe('ai.prompts.title')

    wrapper.unmount()
  })

  it('总数来自 metadata.size 并交给分页组件', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.z-pagination-stub').attributes('data-total')).toBe('42')

    wrapper.unmount()
  })

  it('默认按 7 天、第一页拉取', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 1, perpage: 20, days: 7 })
    expect(lastQuery()?.time_start).toBeUndefined()
    expect(lastQuery()?.time_end).toBeUndefined()

    wrapper.unmount()
  })

  it('选用户即刻重查并回到第一页（与调用日志页一致）', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.z-pagination-stub__next').trigger('click')
    await flushPromises()
    expect(lastQuery()).toMatchObject({ page: 2 })

    await wrapper.find('.z-select-person-stub').setValue(' uid-9 ')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 1, userOid: 'uid-9' })

    wrapper.unmount()
  })

  it('场景筛选逐字传给后端', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await purposeSelect(wrapper).setValue('ocr')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ purpose: 'ocr' })

    wrapper.unmount()
  })

  it('关键字按字面传给后端', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-input-stub').setValue('  本月活动 ')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ keyword: '本月活动' })

    wrapper.unmount()
  })

  it('自定义范围折成 epoch 秒，并整段忽略天数', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-date-picker-stub').setValue('range')
    await flushPromises()

    const query = lastQuery()!
    expect(query.time_start).toBe(RANGE_START_EPOCH)
    expect(query.time_end).toBe(RANGE_END_EPOCH)
    expect(query.days).toBeUndefined()

    wrapper.unmount()
  })

  it('有自定义范围时「最近 N 天」禁用并给 tooltip 说明', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(daysSelect(wrapper).attributes('disabled')).toBeUndefined()

    await wrapper.find('.el-date-picker-stub').setValue('range')
    await flushPromises()

    expect(daysSelect(wrapper).attributes('disabled')).toBeDefined()
    const tooltip = wrapper.find('.el-tooltip-stub')
    expect(tooltip.attributes('data-content')).toBe('ai.prompts.daysDisabled')
    expect(tooltip.attributes('data-disabled')).toBe('false')

    wrapper.unmount()
  })

  it('重新选天数立刻按新窗口拉取', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await daysSelect(wrapper).setValue('30')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 1, days: '30' })

    wrapper.unmount()
  })

  it('AI_LOGS_BAD_FILTER 给「时间范围无效」而不是泛化报错', async () => {
    vi.mocked(listAiPrompts).mockRejectedValueOnce(
      new AiRequestError({ status: 400, code: 'AI_LOGS_BAD_FILTER' })
    )
    const wrapper = mountPage()
    await flushPromises()

    expect(elMessageErrorSpy).toHaveBeenCalledWith('ai.prompts.badFilter')

    wrapper.unmount()
  })

  it('加载失败给空态与重试按钮，重试重新请求', async () => {
    vi.mocked(listAiPrompts).mockRejectedValueOnce(new Error('forbidden'))
    const wrapper = mountPage()
    await flushPromises()

    const empty = wrapper.find('.el-empty-stub')
    expect(empty.text()).toContain('ai.prompts.loadFailed')
    expect(elMessageErrorSpy).toHaveBeenCalled()

    await buttonByText(wrapper, 'ai.prompts.retry').trigger('click')
    await flushPromises()

    expect(vi.mocked(listAiPrompts).mock.calls.length).toBe(2)
    expect(wrapper.find('.el-table-stub').text()).toContain('张三')

    wrapper.unmount()
  })

  it('行点击打开抽屉：读正文、显示访问提示与正文内容', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.el-drawer-stub').exists()).toBe(false)

    await wrapper.find('.el-table-stub__row').trigger('click')
    await flushPromises()

    expect(getAiPrompt).toHaveBeenCalledWith('oid-1')
    const drawer = wrapper.find('.el-drawer-stub')
    expect(drawer.exists()).toBe(true)
    expect(drawer.text()).toContain('ai.prompts.accessNotice')
    expect(drawer.text()).toContain('你好，帮我查一下本月活动。')
    expect(drawer.text()).toContain('req-0001')

    wrapper.unmount()
  })

  it('「查看」按钮只读取一次：点击不穿透成行点击', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.prompts.view').trigger('click')
    await flushPromises()

    expect(getAiPrompt).toHaveBeenCalledTimes(1)
    expect(getAiPrompt).toHaveBeenCalledWith('oid-1')
    expect(wrapper.find('.el-drawer-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('回复文本、工具调用与拒答/警告标记一并展示', async () => {
    vi.mocked(getAiPrompt).mockResolvedValue({
      ...detailFixture,
      answer_text: '本月共有 3 场活动。',
      tool_calls: JSON.stringify([
        {
          name: 'query_activities',
          args_summary: '月份=10',
          result_summary: '3 条',
          status: 'success'
        }
      ]),
      refused: 1,
      warned: 1
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-table-stub__row').trigger('click')
    await flushPromises()

    const drawer = wrapper.find('.el-drawer-stub')
    expect(drawer.text()).toContain('本月共有 3 场活动。')
    expect(drawer.text()).toContain('query_activities')
    expect(drawer.text()).toContain('月份=10')
    expect(drawer.text()).toContain('3 条')
    expect(drawer.text()).toContain('success')
    expect(drawer.find('[data-flag="refused"]').text()).toBe('ai.prompts.refused')
    expect(drawer.find('[data-flag="warned"]').text()).toBe('ai.prompts.warned')

    wrapper.unmount()
  })

  it('tool_calls 解析失败按原文展示，标记位为 0 时不出现', async () => {
    vi.mocked(getAiPrompt).mockResolvedValue({
      ...detailFixture,
      answer_text: null,
      tool_calls: '{broken json',
      refused: 0,
      warned: 0
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-table-stub__row').trigger('click')
    await flushPromises()

    const drawer = wrapper.find('.el-drawer-stub')
    expect(drawer.text()).toContain('{broken json')
    expect(drawer.text()).toContain('ai.prompts.noAnswer')
    expect(drawer.find('[data-flag="refused"]').exists()).toBe(false)
    expect(drawer.find('[data-flag="warned"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('正文加载失败时给错误态，可重试', async () => {
    vi.mocked(getAiPrompt).mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-table-stub__row').trigger('click')
    await flushPromises()

    const drawer = wrapper.find('.el-drawer-stub')
    expect(drawer.text()).toContain('ai.prompts.detailFailed')
    expect(drawer.text()).not.toContain('你好，帮我查一下本月活动。')

    const retry = drawer
      .findAll('.el-button-stub')
      .find((item) => item.text() === 'ai.prompts.retry')!
    await retry.trigger('click')
    await flushPromises()

    expect(vi.mocked(getAiPrompt).mock.calls.length).toBe(2)
    expect(wrapper.find('.el-drawer-stub').text()).toContain('你好，帮我查一下本月活动。')

    wrapper.unmount()
  })
})
