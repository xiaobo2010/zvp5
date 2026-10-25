import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiPromptAudit from './ZAiPromptAudit.vue'
import { getAiPrompt, listAiPrompts } from '@/api/ai'
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

  return {
    ElTable,
    ElTableColumn,
    ElDrawer,
    ElButton,
    ElSelect,
    ElInput,
    ElOption,
    ElIcon,
    ElEmpty,
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
  created_at: '2026-10-24T08:00:00'
}

function mountPage(): VueWrapper {
  return mount(ZAiPromptAudit)
}

function lastQuery() {
  const calls = vi.mocked(listAiPrompts).mock.calls
  return calls[calls.length - 1]![0]
}

beforeEach(() => {
  vi.mocked(listAiPrompts).mockReset()
  vi.mocked(getAiPrompt).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  vi.mocked(listAiPrompts).mockResolvedValue({ rows: [recordFixture], total: 42 })
  vi.mocked(getAiPrompt).mockResolvedValue(detailFixture)
})

describe('行为监控页', () => {
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

    wrapper.unmount()
  })

  it('用户 oid 需要点「查询」才生效，并回到第一页', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.z-pagination-stub__next').trigger('click')
    await flushPromises()
    expect(lastQuery()).toMatchObject({ page: 2 })

    await wrapper.find('.el-input-stub').setValue('  uid-9  ')
    const queryButton = wrapper
      .findAll('.el-button-stub')
      .find((item) => item.text() === 'ai.prompts.query')!
    await queryButton.trigger('click')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 1, userOid: 'uid-9' })

    wrapper.unmount()
  })

  it('切换天数立刻按新窗口重新拉取', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.el-select-stub').setValue('30')
    await flushPromises()

    expect(lastQuery()).toMatchObject({ page: 1, days: '30' })

    wrapper.unmount()
  })

  it('加载失败给空态与重试按钮，重试重新请求', async () => {
    vi.mocked(listAiPrompts).mockRejectedValueOnce(new Error('forbidden'))
    const wrapper = mountPage()
    await flushPromises()

    const empty = wrapper.find('.el-empty-stub')
    expect(empty.text()).toContain('ai.prompts.loadFailed')
    expect(elMessageErrorSpy).toHaveBeenCalled()

    const retry = empty.findAll('.el-button-stub').find((item) => item.text() === 'ai.prompts.retry')!
    await retry.trigger('click')
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

    const viewButton = wrapper
      .findAll('.el-button-stub')
      .find((item) => item.text() === 'ai.prompts.view')!
    await viewButton.trigger('click')
    await flushPromises()

    expect(getAiPrompt).toHaveBeenCalledTimes(1)
    expect(getAiPrompt).toHaveBeenCalledWith('oid-1')
    expect(wrapper.find('.el-drawer-stub').exists()).toBe(true)

    wrapper.unmount()
  })

  it('正文加载失败时给错误态，可重试', async () => {    vi.mocked(getAiPrompt).mockRejectedValueOnce(new Error('boom'))
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
