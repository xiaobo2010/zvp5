import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZUserModification from './ZUserModification.vue'

/**
 * 用户建号 / 修改页的学号字段。
 *
 * 学号在后端是「非空、≤32 字符、不限数字」的字符串，页面必须原样承载：
 * number 会截断 >16 位的学号、把非数字学号挡在输入阶段。这两条路径在这里钉住。
 * api 与 element-plus 都是替身（真实 element-plus 会连带 theme-chalk 的 css）。
 */

const { apiMock, messageMock, notificationMock } = vi.hoisted(() => ({
  apiMock: {
    user: {
      readOne_v3: vi.fn(),
      create_v3: vi.fn(),
      updateStudentId: vi.fn(),
      updateRoles: vi.fn()
    },
    group: {
      read: vi.fn(),
      read_v2: vi.fn(),
      readOne: vi.fn(),
      addGroupUserV3: vi.fn(),
      removeGroupUserV3: vi.fn()
    }
  },
  messageMock: vi.fn(),
  notificationMock: vi.fn()
}))

vi.mock('@/api', () => ({ default: apiMock }))

// 学号输入框不依赖剪贴板；这里只保留组件用到的 copy / copied。
vi.mock('@vueuse/core', async () => {
  const { ref } = await import('vue')
  return { useClipboard: () => ({ copy: vi.fn(), copied: ref(false) }) }
})

vi.mock('@/stores/user.ts', () => ({
  useUserStore: () => ({ _id: 'admin-oid', refreshUser: vi.fn() })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElCard = defineComponent({
    name: 'ElCard',
    setup(_, { slots }) {
      return () => h('div', { class: 'el-card-stub' }, slots.default?.())
    }
  })

  const ElForm = defineComponent({
    name: 'ElForm',
    setup(_, { slots }) {
      return () => h('form', slots.default?.())
    }
  })

  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    props: { label: String, required: Boolean },
    setup(_, { slots }) {
      return () => h('div', { class: 'el-form-item-stub' }, slots.default?.())
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: { modelValue: { type: [String, Number], default: '' } },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-stub',
          value: props.modelValue,
          onInput: (event: Event) =>
            emit('update:modelValue', (event.target as HTMLInputElement).value)
        })
    }
  })

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: { type: String, default: '' } },
    setup(props, { slots }) {
      return () => h('div', { class: 'el-select-stub', 'data-value': props.modelValue }, slots.default?.())
    }
  })

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: String },
    setup() {
      return () => null
    }
  })

  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, text: Boolean, bg: Boolean, loading: Boolean, disabled: Boolean },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || props.loading || undefined,
            onClick: (event: MouseEvent) => {
              if (props.disabled || props.loading) {
                return
              }
              emit('click', event)
            }
          },
          slots.default?.()
        )
    }
  })

  const ElCheckboxGroup = defineComponent({
    name: 'ElCheckboxGroup',
    props: { modelValue: { type: Array, default: () => [] } },
    setup(_, { slots }) {
      return () => h('div', { class: 'el-checkbox-group-stub' }, slots.default?.())
    }
  })

  const ElCheckbox = defineComponent({
    name: 'ElCheckbox',
    props: { label: String },
    setup(props) {
      return () => h('label', { class: 'el-checkbox-stub' }, props.label)
    }
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String },
    setup(props, { slots }) {
      return () =>
        props.modelValue ? h('div', { class: 'el-dialog-stub' }, slots.default?.()) : null
    }
  })

  return {
    ElCard,
    ElForm,
    ElFormItem,
    ElInput,
    ElSelect,
    ElOption,
    ElButton,
    ElCheckboxGroup,
    ElCheckbox,
    ElDialog,
    ElLoading: { service: () => ({ close: vi.fn() }) },
    ElMessage: Object.assign(messageMock, {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElNotification: Object.assign(notificationMock, {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn()
    })
  }
})

const CLASS_ID = 'a'.repeat(24)

function userRow(overrides: Record<string, unknown> = {}) {
  return {
    _id: 'user-oid',
    id: '20240001',
    name: '张同学',
    group: [],
    past: [],
    roles: [],
    class_id: CLASS_ID,
    ...overrides
  }
}

/** 名称输入框是第 1 个 .el-input-stub，学号是第 2 个。 */
function inputAt(wrapper: VueWrapper, index: number) {
  return wrapper.findAll('.el-input-stub')[index]
}

function submitButton(wrapper: VueWrapper) {
  return wrapper.find('.el-button-stub')
}

beforeEach(() => {
  messageMock.mockReset()
  notificationMock.mockReset()
  for (const fn of Object.values(apiMock.user)) {
    fn.mockReset()
  }
  for (const fn of Object.values(apiMock.group)) {
    fn.mockReset()
  }
  apiMock.group.read.mockResolvedValue({ groups: [] })
  apiMock.user.updateStudentId.mockResolvedValue(true)
  apiMock.user.updateRoles.mockResolvedValue(true)
})

describe('用户建号 / 修改的学号字段', () => {
  it('建号时非数字学号可输入，并原样（字符串）进入 create_v3', async () => {
    apiMock.user.create_v3.mockResolvedValue({ id: 'cs2019A' })
    const wrapper = mount(ZUserModification, {
      props: { id: '', mode: 'create', cid: CLASS_ID }
    })
    await flushPromises()

    await inputAt(wrapper, 0).setValue('李同学')
    await inputAt(wrapper, 1).setValue('cs2019A')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.user.create_v3).toHaveBeenCalledWith('cs2019A', '李同学', CLASS_ID)
    expect(notificationMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'manage.personalPanel.createdNotification' })
    )
    wrapper.unmount()
  })

  it('32 位学号回显不丢精度，改动后按字符串提交 updateStudentId', async () => {
    const longId = 'Z'.repeat(32)
    apiMock.user.readOne_v3.mockResolvedValue(userRow({ id: longId }))
    const wrapper = mount(ZUserModification, {
      props: { id: 'user-oid', mode: 'modify', cid: CLASS_ID }
    })
    await flushPromises()

    // 回显是完整字符串，不是 1e+31 之类的科学计数法
    expect((inputAt(wrapper, 1).element as HTMLInputElement).value).toBe(longId)

    await inputAt(wrapper, 1).setValue('cs2019B')
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.user.updateStudentId).toHaveBeenCalledWith('user-oid', 'cs2019B')
    wrapper.unmount()
  })

  it('学号超长时不提交，只提示校验文案', async () => {
    apiMock.user.readOne_v3.mockResolvedValue(userRow())
    const wrapper = mount(ZUserModification, {
      props: { id: 'user-oid', mode: 'modify', cid: CLASS_ID }
    })
    await flushPromises()

    await inputAt(wrapper, 1).setValue('A'.repeat(33))
    await submitButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.user.updateStudentId).not.toHaveBeenCalled()
    expect(messageMock).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'manage.personalPanel.studentIdInvalid' })
    )
    wrapper.unmount()
  })
})
