import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZGroupList from './ZGroupList.vue'

/**
 * 群组列表的两个入口：
 * - 「添加班级」：单输入框弹窗，必填校验后调 createGroupV3，成功后刷新列表；
 * - 重命名按钮已移除（后端 PUT /v1/groups/{id}/name 无前端入口时不保留死按钮）。
 */

const { readSpy, createSpy, deleteSpy, pushSpy, elMessageErrorSpy, elMessageSuccessSpy } =
  vi.hoisted(() => ({
    readSpy: vi.fn(),
    createSpy: vi.fn(),
    deleteSpy: vi.fn(),
    pushSpy: vi.fn(),
    elMessageErrorSpy: vi.fn(),
    elMessageSuccessSpy: vi.fn()
  }))

vi.mock('@/api', () => ({
  default: {
    group: {
      read_v2: readSpy,
      createGroupV3: createSpy,
      delete: deleteSpy
    }
  }
}))

vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => ({ _id: 'me-oid', position: ['admin'] })
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushSpy })
}))

// 真实中文 locale 当翻译器：断言写人话文案，才能证明弹窗与报错确实落到中文。
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

vi.mock('@/components/utils/ZPagination.vue', () => ({
  default: {
    name: 'ZPaginationStub',
    props: { currentPage: Number, pageSize: Number, total: Number },
    setup() {
      return () => null
    }
  }
}))

vi.mock('@/components/tags/ZUserPosition.vue', () => ({
  default: {
    name: 'ZUserPositionStub',
    props: { position: { type: Array, default: () => [] } },
    setup() {
      return () => null
    }
  }
}))

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h, inject, provide } = await import('vue')

  function passthrough(name: string, tag = 'div') {
    return defineComponent({
      name,
      setup(_, { slots, attrs }) {
        return () => h(tag, attrs, slots.default?.())
      }
    })
  }

  const ElTable = defineComponent({
    name: 'ElTable',
    props: { data: { type: Array, default: () => [] }, maxHeight: Number },
    setup(props, { slots }) {
      // 行的数据源交给列自行取用（真实 ElTable 也是父级持有 data、列只负责取 row）
      provide('elTableRows', () => props.data as Array<Record<string, unknown>>)
      return () => h('div', { class: 'el-table-stub' }, slots.default?.())
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
        h(
          'div',
          { class: 'el-table-column-stub', 'data-column': props.label },
          rows().map((row) =>
            h(
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
    props: {
      modelValue: Boolean,
      title: String,
      width: String,
      closeOnClickModal: Boolean,
      closeOnPressEscape: Boolean,
      showClose: Boolean
    },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub' }, [
              h('header', { class: 'el-dialog-stub__title' }, props.title),
              slots.default?.(),
              slots.footer?.()
            ])
          : null
    }
  })

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

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: String, default: '' },
      placeholder: String,
      maxlength: [Number, String],
      clearable: Boolean,
      size: String,
      prefixIcon: { type: [Object, String, Function], default: undefined }
    },
    emits: ['update:modelValue'],
    setup(props, { emit, attrs }) {
      return () =>
        h('input', {
          ...attrs,
          class: ['el-input-stub', attrs.class],
          value: props.modelValue,
          placeholder: props.placeholder,
          onInput: (event: Event) => {
            emit('update:modelValue', (event.target as HTMLInputElement).value)
          }
        })
    }
  })

  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    props: { label: String, error: String, required: Boolean },
    setup(props, { slots }) {
      return () =>
        h('div', { class: 'el-form-item-stub' }, [
          h('label', { class: 'el-form-item-stub__label' }, props.label),
          slots.default?.(),
          props.error ? h('span', { class: 'el-form-item-stub__error' }, props.error) : null
        ])
    }
  })

  return {
    ElTable,
    ElTableColumn,
    ElDialog,
    ElButton,
    ElInput,
    ElFormItem,
    ElForm: passthrough('ElForm', 'form'),
    ElCard: passthrough('ElCard'),
    ElTooltip: passthrough('ElTooltip'),
    ElIcon: passthrough('ElIcon', 'i'),
    ElMessage: Object.assign(vi.fn(), {
      success: elMessageSuccessSpy,
      error: elMessageErrorSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: { confirm: vi.fn() },
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {}, unmounted() {} }
  }
})

const GROUPS = [
  { _id: 'g1', name: '高一（1）班', permissions: [] },
  { _id: 'g2', name: '高一（2）班', permissions: [] }
]

async function mountList(): Promise<VueWrapper> {
  const wrapper = mount(ZGroupList)
  await flushPromises()
  return wrapper
}

function addButton(wrapper: VueWrapper) {
  return wrapper
    .findAll('.el-button-stub')
    .find((item) => item.text().includes('添加班级'))!
}

function dialogButtons(wrapper: VueWrapper, text: string) {
  return wrapper
    .find('.el-dialog-stub')
    .findAll('.el-button-stub')
    .filter((item) => item.text().includes(text))
}

beforeEach(() => {
  readSpy.mockReset()
  createSpy.mockReset()
  deleteSpy.mockReset()
  pushSpy.mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()

  readSpy.mockResolvedValue({ groups: GROUPS, size: GROUPS.length })
  createSpy.mockResolvedValue('g3')
})

describe('群组列表', () => {
  it('工具栏只留「添加班级」：重命名按钮已移除', async () => {
    const wrapper = await mountList()

    expect(wrapper.text()).toContain('添加班级')
    expect(wrapper.text()).not.toContain('重命名')
    // 行内操作仍在：查看与删除
    expect(wrapper.find('.el-table-stub').text()).toContain('查看')
    expect(wrapper.find('.el-table-stub').text()).toContain('删除')

    wrapper.unmount()
  })

  it('点「添加班级」打开弹窗，空名不提交并给出必填提示', async () => {
    const wrapper = await mountList()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
    await addButton(wrapper).trigger('click')

    const dialog = wrapper.find('.el-dialog-stub')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('添加班级')

    await dialogButtons(wrapper, '创建')[0]!.trigger('click')
    await flushPromises()

    expect(createSpy).not.toHaveBeenCalled()
    expect(dialog.find('.el-form-item-stub__error').text()).toContain('请输入班级名称')

    wrapper.unmount()
  })

  it('提交班级名调 createGroupV3 并刷新列表', async () => {
    const wrapper = await mountList()
    await addButton(wrapper).trigger('click')

    await wrapper.find('.el-input-stub').setValue('高一（3）班')
    await dialogButtons(wrapper, '创建')[0]!.trigger('click')
    await flushPromises()

    expect(createSpy).toHaveBeenCalledWith('高一（3）班')
    expect(elMessageSuccessSpy).toHaveBeenCalledWith('已创建班级「高一（3）班」')
    expect(readSpy).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)

    wrapper.unmount()
  })

  it('名称两端空白被裁掉后再提交', async () => {
    const wrapper = await mountList()
    await addButton(wrapper).trigger('click')

    await wrapper.find('.el-input-stub').setValue('  高一（4）班  ')
    await dialogButtons(wrapper, '创建')[0]!.trigger('click')
    await flushPromises()

    expect(createSpy).toHaveBeenCalledWith('高一（4）班')

    wrapper.unmount()
  })

  it('后端 4xx 的 detail 透传到 ElMessage.error，弹窗保持打开', async () => {
    createSpy.mockRejectedValueOnce({ response: { data: { detail: '班级已存在' } } })
    const wrapper = await mountList()
    await addButton(wrapper).trigger('click')

    await wrapper.find('.el-input-stub').setValue('高一（1）班')
    await dialogButtons(wrapper, '创建')[0]!.trigger('click')
    await flushPromises()

    expect(elMessageErrorSpy).toHaveBeenCalledWith('班级已存在')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    expect(readSpy).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  // 后端空响应（200 但没有 body）曾让 loading 永久为真，表格一直转圈
  it('空响应也要收掉 loading', async () => {
    readSpy.mockResolvedValueOnce(undefined)
    const wrapper = await mountList()

    expect((wrapper.vm as unknown as { loading: boolean }).loading).toBe(false)

    wrapper.unmount()
  })
})
