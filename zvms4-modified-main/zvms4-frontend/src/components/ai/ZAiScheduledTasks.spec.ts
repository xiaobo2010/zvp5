import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZAiScheduledTasks from './ZAiScheduledTasks.vue'
import {
  createAiScheduledTask,
  deleteAiScheduledTask,
  listAiScheduledTaskRuns,
  listAiScheduledTasks,
  reviewAiScheduledTask,
  runAiScheduledTaskNow,
  updateAiScheduledTask
} from '@/api/ai'
import { AiRequestError } from '@/api/ai/errors'
import type { AiScheduledTask, AiScheduledTaskRun } from '@/../types/ai'

const { elMessageErrorSpy, elMessageSuccessSpy, elMessageWarningSpy, elMessageBoxConfirmSpy } =
  vi.hoisted(() => ({
    elMessageErrorSpy: vi.fn(),
    elMessageSuccessSpy: vi.fn(),
    elMessageWarningSpy: vi.fn(),
    elMessageBoxConfirmSpy: vi.fn()
  }))

const { userState } = vi.hoisted(() => ({
  userState: { position: [] as string[] }
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    listAiScheduledTasks: vi.fn(),
    createAiScheduledTask: vi.fn(),
    updateAiScheduledTask: vi.fn(),
    deleteAiScheduledTask: vi.fn(),
    reviewAiScheduledTask: vi.fn(),
    runAiScheduledTaskNow: vi.fn(),
    listAiScheduledTaskRuns: vi.fn()
  }
})

vi.mock('@/stores/user', async () => {
  const { reactive } = await import('vue')
  return {
    useUserStore: () => reactive(userState)
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
      props: { modelValue: { type: [String, Array], default: '' }, filterStart: Number, fullWidth: Boolean },
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
          h('div', { class: 'z-pagination-stub', 'data-total': String(props.total) }, [
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
          ])
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

  const ElDrawer = defineComponent({
    name: 'ElDrawer',
    props: { modelValue: Boolean, title: String, size: String, direction: String },
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
      return () =>
        h('div', { class: 'el-form-item-stub', 'data-prop': props.prop }, [
          h('span', { class: 'el-form-item-stub__label' }, props.label),
          ...(slots.default?.() ?? [])
        ])
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: [String, Number], default: '' },
      placeholder: String,
      type: String
    },
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
    props: { modelValue: { type: Number, default: 0 }, min: Number, max: Number, step: Number },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-number-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', Number((event.target as HTMLInputElement).value))
        })
    }
  })

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: { type: [String, Number], default: '' }, placeholder: String },
    emits: ['update:modelValue', 'change'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-select-stub',
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) => {
            const value = (event.target as HTMLInputElement).value
            emit('update:modelValue', value)
            emit('change', value)
          }
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

  const ElRadioGroup = defineComponent({
    name: 'ElRadioGroup',
    props: { modelValue: { type: [String, Number, Boolean], default: '' } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-radio-group-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElRadio = defineComponent({
    name: 'ElRadio',
    props: { value: [String, Number], label: String },
    setup(props, { slots }) {
      return () =>
        h('label', { class: 'el-radio-stub', 'data-value': String(props.value ?? '') }, [
          ...(slots.default?.() ?? []),
          props.label
        ])
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
            disabled: props.disabled || undefined,
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

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String },
    setup(_, { slots }) {
      return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
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
      return () => h('div', { class: 'el-empty-stub' }, [props.description, slots.default?.()])
    }
  })

  return {
    ElTable,
    ElTableColumn,
    ElButton,
    ElDialog,
    ElDrawer,
    ElForm,
    ElFormItem,
    ElInput,
    ElInputNumber,
    ElSelect,
    ElOption,
    ElRadioGroup,
    ElRadio,
    ElSwitch,
    ElTag,
    ElTooltip,
    ElIcon,
    ElEmpty,
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: elMessageWarningSpy,
      info: vi.fn()
    }),
    ElMessageBox: Object.assign(vi.fn(), {
      confirm: elMessageBoxConfirmSpy
    })
  }
})

const pendingTask: AiScheduledTask = {
  oid: 'task-1',
  owner_oid: 'uid-1',
  name: '每日活动播报',
  prompt_text: '请播报今天的活动安排',
  review_status: 'pending',
  reviewed_by_oid: null,
  reviewed_at: null,
  review_note: null,
  schedule_kind: 'interval',
  interval_minutes: 60,
  cron_expr: null,
  enabled: true,
  model: null,
  last_run_at: null,
  next_run_at: '2026-10-28T09:00:00',
  run_count: 3,
  last_status: 'queued',
  last_error: null,
  created_at: '2026-10-24T08:00:00',
  updated_at: '2026-10-24T08:00:00'
}

const approvedTask: AiScheduledTask = {
  ...pendingTask,
  oid: 'task-2',
  name: '夜间巡检',
  review_status: 'approved',
  schedule_kind: 'cron',
  cron_expr: '0 9 * * *',
  interval_minutes: 1440,
  run_count: 7,
  last_status: 'skipped_quota',
  last_error: 'monthly chat quota exhausted (1/1)'
}

const runFixture: AiScheduledTaskRun = {
  run_id: 'run-1',
  status: 'failed',
  model: 'gpt-4o-mini',
  stop_reason: 'error',
  error_code: 'AI_UPSTREAM_ERROR',
  error_message: '上游服务异常',
  created_at: '2026-10-24T09:00:00',
  started_at: '2026-10-24T09:00:01',
  finished_at: '2026-10-24T09:00:12'
}

function mountPage(positions: string[] = ['department']): VueWrapper {
  userState.position = positions
  return mount(ZAiScheduledTasks)
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('.el-button-stub').find((item) => item.text() === text)!
}

function rowByName(wrapper: VueWrapper, name: string) {
  return wrapper.findAll('.el-table-stub__row').find((row) => row.text().includes(name))!
}

beforeEach(() => {
  vi.mocked(listAiScheduledTasks).mockReset()
  vi.mocked(createAiScheduledTask).mockReset()
  vi.mocked(updateAiScheduledTask).mockReset()
  vi.mocked(deleteAiScheduledTask).mockReset()
  vi.mocked(reviewAiScheduledTask).mockReset()
  vi.mocked(runAiScheduledTaskNow).mockReset()
  vi.mocked(listAiScheduledTaskRuns).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  elMessageWarningSpy.mockReset()
  elMessageBoxConfirmSpy.mockReset()
  elMessageBoxConfirmSpy.mockResolvedValue(undefined)
  vi.mocked(listAiScheduledTasks).mockResolvedValue({
    rows: [pendingTask, approvedTask],
    size: 2
  })
  vi.mocked(createAiScheduledTask).mockResolvedValue(pendingTask)
  vi.mocked(updateAiScheduledTask).mockResolvedValue(pendingTask)
  vi.mocked(reviewAiScheduledTask).mockResolvedValue(pendingTask)
  vi.mocked(runAiScheduledTaskNow).mockResolvedValue({ run_id: 'run-1', status: 'queued' })
  vi.mocked(listAiScheduledTaskRuns).mockResolvedValue({ rows: [runFixture], total: 1 })
})

describe('定时任务列表', () => {
  it('渲染名称、审核状态、最近状态、排程与执行次数', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const table = wrapper.find('.el-table-stub')
    expect(table.text()).toContain('每日活动播报')
    expect(table.text()).toContain('ai.schedule.review.pending')
    expect(table.text()).toContain('ai.schedule.review.approved')
    expect(table.text()).toContain('ai.schedule.last.queued')
    expect(table.text()).toContain('ai.schedule.last.skipped_quota')
    expect(table.text()).toContain('ai.schedule.plan.interval')
    expect(table.text()).toContain('ai.schedule.plan.cron')
    expect(table.text()).toContain('2026-10-28 09:00')

    wrapper.unmount()
  })

  it('加载失败时给错误态空页面并弹错误提示', async () => {
    vi.mocked(listAiScheduledTasks).mockRejectedValue(
      new AiRequestError({ status: 500, code: 'AI_SCHEDULE_NOT_FOUND' })
    )
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.el-empty-stub').text()).toContain('ai.schedule.loadFailed')
    expect(elMessageErrorSpy).toHaveBeenCalled()
    expect(elMessageErrorSpy.mock.calls[0]![0]).toContain('ai.errors.AI_SCHEDULE_NOT_FOUND.title')

    wrapper.unmount()
  })

  it('部门角色显示已用条数，管理员显示不限', async () => {
    const wrapper = mountPage(['department'])
    await flushPromises()
    expect(wrapper.text()).toContain('ai.schedule.quotaUsed')
    expect(wrapper.text()).not.toContain('ai.schedule.quotaUnlimited')
    wrapper.unmount()

    const admin = mountPage(['admin'])
    await flushPromises()
    expect(admin.text()).toContain('ai.schedule.quotaUnlimited')
    admin.unmount()
  })

  it('管理员默认走 all_tasks 全局列表', async () => {
    const wrapper = mountPage(['admin'])
    await flushPromises()

    expect(listAiScheduledTasks).toHaveBeenLastCalledWith({
      allTasks: true,
      limit: 50,
      offset: 0
    })

    wrapper.unmount()
  })

  it('管理员区按 owner oid 查询他人的任务（叠加在全局列表上）', async () => {
    const wrapper = mountPage(['admin'])
    await flushPromises()

    await wrapper.find('.z-select-person-stub').setValue('uid-9')
    await flushPromises()

    expect(listAiScheduledTasks).toHaveBeenLastCalledWith({
      allTasks: true,
      limit: 50,
      offset: 0,
      ownerOid: 'uid-9'
    })

    wrapper.unmount()
  })

  it('审核状态过滤交给服务端（review_status 进 query）', async () => {
    const wrapper = mountPage(['admin'])
    await flushPromises()

    await wrapper.find('.el-select-stub').setValue('approved')
    await flushPromises()

    expect(listAiScheduledTasks).toHaveBeenLastCalledWith({
      allTasks: true,
      reviewStatus: 'approved',
      limit: 50,
      offset: 0
    })

    wrapper.unmount()
  })

  it('非管理员不带任何管理端参数', async () => {
    const wrapper = mountPage(['department'])
    await flushPromises()

    expect(listAiScheduledTasks).toHaveBeenCalledWith({})

    wrapper.unmount()
  })

  it('本页打满 limit 时出现「加载更多」，点它按 offset 追加而不是替换', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({
      ...pendingTask,
      oid: `task-${index}`,
      name: `任务 ${index}`
    }))
    vi.mocked(listAiScheduledTasks)
      .mockResolvedValueOnce({ rows: firstPage, size: 50 })
      .mockResolvedValueOnce({
        rows: [{ ...pendingTask, oid: 'task-50', name: '任务 50' }],
        size: 1
      })

    const wrapper = mountPage(['admin'])
    await flushPromises()

    const moreButton = wrapper
      .findAll('.el-button-stub')
      .find((button) => button.text() === 'ai.schedule.loadMore')
    expect(moreButton).toBeDefined()
    expect(wrapper.find('.el-table-stub').text()).toContain('任务 0')

    await moreButton!.trigger('click')
    await flushPromises()

    expect(listAiScheduledTasks).toHaveBeenLastCalledWith({
      allTasks: true,
      limit: 50,
      offset: 50
    })
    // 追加而不是替换：前 50 行还在，第 51 行也进来了
    const table = wrapper.find('.el-table-stub')
    expect(table.text()).toContain('任务 0')
    expect(table.text()).toContain('任务 50')

    // 不满 limit 就没有下一页了
    expect(
      wrapper.findAll('.el-button-stub').find((button) => button.text() === 'ai.schedule.loadMore')
    ).toBeUndefined()

    wrapper.unmount()
  })

  it('管理端查询被拒（403）给可读文案', async () => {
    vi.mocked(listAiScheduledTasks).mockRejectedValue(
      new AiRequestError({ status: 403, code: 'AI_SCHEDULE_FORBIDDEN' })
    )
    const wrapper = mountPage(['admin'])
    await flushPromises()

    expect(elMessageErrorSpy).toHaveBeenCalled()
    // 人话文案（标题 + 建议动作），不是裸错误码
    expect(elMessageErrorSpy.mock.calls[0]![0]).toBe(
      'ai.errors.AI_SCHEDULE_FORBIDDEN.title：ai.errors.AI_SCHEDULE_FORBIDDEN.hint'
    )

    wrapper.unmount()
  })

  it('审核状态取值非法（422）给可读文案', async () => {
    vi.mocked(listAiScheduledTasks).mockRejectedValue(
      new AiRequestError({ status: 422, code: 'AI_SCHEDULE_BAD_STATUS' })
    )
    const wrapper = mountPage(['admin'])
    await flushPromises()

    expect(elMessageErrorSpy).toHaveBeenCalled()
    expect(elMessageErrorSpy.mock.calls[0]![0]).toBe(
      'ai.errors.AI_SCHEDULE_BAD_STATUS.title：ai.errors.AI_SCHEDULE_BAD_STATUS.hint'
    )

    wrapper.unmount()
  })
})

describe('定时任务表单', () => {
  it('新建时按后端字段提交', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.create').trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    const inputs = dialog.findAll('.el-input-stub')
    await inputs[0]!.setValue('每日播报')
    await inputs[1]!.setValue('请播报今天的活动安排')

    await buttonByText(wrapper, 'ai.common.save').trigger('click')
    await flushPromises()

    expect(createAiScheduledTask).toHaveBeenCalledWith({
      name: '每日播报',
      prompt_text: '请播报今天的活动安排',
      schedule_kind: 'interval',
      interval_minutes: 1440,
      cron_expr: null,
      model: null
    })
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.schedule.createdNotice')

    wrapper.unmount()
  })

  it('提示词为空时不发请求', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.create').trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-stub')[0]!.setValue('每日播报')

    await buttonByText(wrapper, 'ai.common.save').trigger('click')
    await flushPromises()

    expect(createAiScheduledTask).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('切换到 CRON 后提交表达式，非法表达式不发请求', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.create').trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-stub')[0]!.setValue('每日播报')
    await dialog.findAll('.el-input-stub')[1]!.setValue('请播报今天的活动安排')

    await wrapper.find('.el-radio-group-stub').setValue('cron')
    await flushPromises()

    await buttonByText(wrapper, 'ai.common.save').trigger('click')
    await flushPromises()

    expect(createAiScheduledTask).toHaveBeenCalledWith(
      expect.objectContaining({ schedule_kind: 'cron', cron_expr: '0 9 * * *' })
    )

    wrapper.unmount()
  })

  it('非法 CRON 表达式不发请求', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.create').trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-stub')[0]!.setValue('每日播报')
    await dialog.findAll('.el-input-stub')[1]!.setValue('请播报今天的活动安排')

    await wrapper.find('.el-radio-group-stub').setValue('cron')
    await flushPromises()

    await dialog.findAll('.el-input-stub')[2]!.setValue('99 9 * * *')
    await buttonByText(wrapper, 'ai.common.save').trigger('click')
    await flushPromises()

    expect(createAiScheduledTask).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('间隔小于 15 分钟不发请求', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.create').trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-stub')[0]!.setValue('每日播报')
    await dialog.findAll('.el-input-stub')[1]!.setValue('请播报今天的活动安排')

    await dialog.find('.el-input-number-stub').setValue('5')
    await buttonByText(wrapper, 'ai.common.save').trigger('click')
    await flushPromises()

    expect(createAiScheduledTask).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('配额超限（409）给可读文案而不是错误码', async () => {
    vi.mocked(createAiScheduledTask).mockRejectedValue(
      new AiRequestError({ status: 409, code: 'AI_SCHEDULE_QUOTA_EXCEEDED' })
    )
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.create').trigger('click')
    const dialog = wrapper.find('.el-dialog-stub')
    await dialog.findAll('.el-input-stub')[0]!.setValue('每日播报')
    await dialog.findAll('.el-input-stub')[1]!.setValue('请播报今天的活动安排')

    await buttonByText(wrapper, 'ai.common.save').trigger('click')
    await flushPromises()

    const shown = wrapper.find('.el-dialog-stub').text()
    expect(shown).toContain('ai.schedule.quotaExceeded')
    expect(shown).not.toContain('AI_SCHEDULE_QUOTA_EXCEEDED')

    wrapper.unmount()
  })

  it('编辑时提示「修改提示词将重新进入审核」', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    await row.findAll('.el-button-stub').find((b) => b.text() === 'ai.common.edit')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog-stub')
    expect(dialog.text()).not.toContain('ai.schedule.promptChanged')

    await dialog.findAll('.el-input-stub')[1]!.setValue('换了新的文案')
    await flushPromises()

    expect(wrapper.find('.el-dialog-stub').text()).toContain('ai.schedule.promptChanged')

    wrapper.unmount()
  })
})

describe('定时任务操作', () => {
  it('未审核通过的任务不能立即执行', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    const runButton = row.findAll('.el-button-stub').find((b) => b.text() === 'ai.schedule.runNow')!
    expect(runButton.attributes('disabled')).toBeDefined()

    await runButton.trigger('click')
    await flushPromises()

    expect(runAiScheduledTaskNow).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('审核通过的任务立即执行会调后端', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '夜间巡检')
    const runButton = row.findAll('.el-button-stub').find((b) => b.text() === 'ai.schedule.runNow')!
    expect(runButton.attributes('disabled')).toBeUndefined()

    await runButton.trigger('click')
    await flushPromises()

    expect(runAiScheduledTaskNow).toHaveBeenCalledWith('task-2')
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.schedule.runNotice')

    wrapper.unmount()
  })

  it('开关切换启停状态', async () => {
    vi.mocked(updateAiScheduledTask).mockResolvedValue({ ...pendingTask, enabled: false })
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    await row.find('.el-switch-stub').trigger('click')
    await flushPromises()

    expect(updateAiScheduledTask).toHaveBeenCalledWith('task-1', { enabled: false })

    wrapper.unmount()
  })

  it('删除要二次确认，确认后才调后端', async () => {
    vi.mocked(deleteAiScheduledTask).mockResolvedValue(undefined)
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    await row.findAll('.el-button-stub').find((b) => b.text() === 'ai.common.delete')!.trigger('click')
    await flushPromises()

    expect(elMessageBoxConfirmSpy).toHaveBeenCalled()
    expect(deleteAiScheduledTask).toHaveBeenCalledWith('task-1')

    wrapper.unmount()
  })

  it('取消确认时不删任务', async () => {
    elMessageBoxConfirmSpy.mockRejectedValueOnce(new Error('cancel'))
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    await row.findAll('.el-button-stub').find((b) => b.text() === 'ai.common.delete')!.trigger('click')
    await flushPromises()

    expect(deleteAiScheduledTask).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('管理员审核：提交结论与备注', async () => {
    const wrapper = mountPage(['admin'])
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    await row.findAll('.el-button-stub').find((b) => b.text() === 'ai.schedule.reviewAction')!.trigger('click')
    await flushPromises()

    const dialog = wrapper.findAll('.el-dialog-stub')[0]!
    await dialog.find('.el-radio-group-stub').setValue('rejected')
    await dialog.find('.el-input-stub').setValue('文案写错了')
    await flushPromises()

    await buttonByText(wrapper, 'ai.schedule.reviewDialog.submit').trigger('click')
    await flushPromises()

    expect(reviewAiScheduledTask).toHaveBeenCalledWith('task-1', 'rejected', '文案写错了')
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('ai.schedule.reviewNotice')

    wrapper.unmount()
  })

  it('非管理员看不到审核入口', async () => {
    const wrapper = mountPage(['department'])
    await flushPromises()

    expect(wrapper.text()).not.toContain('ai.schedule.reviewAction')
    expect(wrapper.text()).not.toContain('ai.schedule.admin.title')

    wrapper.unmount()
  })

  it('历史抽屉按 oid 拉执行记录并显示失败原因', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const row = rowByName(wrapper, '每日活动播报')
    await row.findAll('.el-button-stub').find((b) => b.text() === 'ai.schedule.history')!.trigger('click')
    await flushPromises()

    expect(listAiScheduledTaskRuns).toHaveBeenCalledWith('task-1', 1, 20)
    const drawer = wrapper.find('.el-drawer-stub')
    expect(drawer.exists()).toBe(true)
    expect(drawer.text()).toContain('上游服务异常')
    expect(drawer.text()).toContain('ai.schedule.runs.statusFailed')

    wrapper.unmount()
  })
})
