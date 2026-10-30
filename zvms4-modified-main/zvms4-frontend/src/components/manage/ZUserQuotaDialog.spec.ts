import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZUserQuotaDialog from './ZUserQuotaDialog.vue'
import { resetUserQuota, setUserLimit, userQuota } from '@/api/ai'
import { getUserQuota, setUserQuota } from '@/api/system/userQuotas'
import type { UserQuotaView } from '@/api/system/userQuotas'

const { elMessageErrorSpy, elMessageSuccessSpy, confirmSpy } = vi.hoisted(() => ({
  elMessageErrorSpy: vi.fn(),
  elMessageSuccessSpy: vi.fn(),
  confirmSpy: vi.fn()
}))

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...actual,
    userQuota: vi.fn(),
    setUserLimit: vi.fn(),
    resetUserQuota: vi.fn()
  }
})

vi.mock('@/api/system/userQuotas', () => ({
  getUserQuota: vi.fn(),
  setUserQuota: vi.fn()
}))

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

// element-plus 整包 mock：真实包会连带引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String, width: String },
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
    props: { type: String, size: String, loading: Boolean, disabled: Boolean, text: Boolean },
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

  const ElInputNumber = defineComponent({
    name: 'ElInputNumber',
    props: { modelValue: { default: undefined }, min: Number, step: Number, placeholder: String },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          class: 'el-input-number-stub',
          value: props.modelValue ?? '',
          placeholder: props.placeholder,
          onInput: (event: Event) => {
            const raw = (event.target as HTMLInputElement).value
            emit('update:modelValue', raw === '' ? null : Number(raw))
          }
        })
    }
  })

  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, size: String, effect: String },
    setup(props, { slots }) {
      return () =>
        h(
          'span',
          { class: 'el-tag-stub', 'data-type': props.type ?? '' },
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

  return {
    ElDialog,
    ElButton,
    ElInputNumber,
    ElTag,
    ElIcon,
    ElLoadingDirective: { name: 'loading', mounted() {}, updated() {} },
    ElMessage: Object.assign(vi.fn(), {
      error: elMessageErrorSpy,
      success: elMessageSuccessSpy,
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: { confirm: confirmSpy }
  }
})

const aiFixture = {
  af: { period: '202610', used: 7, limit: 500, source: 'explicit' as const }
}

const uploadFixture: UserQuotaView = {
  user_oid: 'user-1',
  daily_max_files: null,
  daily_max_bytes: null,
  effective: { daily_max_files: 50, daily_max_bytes: 104857600 }
}

async function mountDialog(): Promise<VueWrapper> {
  const wrapper = mount(ZUserQuotaDialog, {
    props: { visible: true, user: { oid: 'user-1', name: '张三' } }
  })
  await flushPromises()
  return wrapper
}

/** ElInputNumber 的渲染顺序：AF 上限、每日文件数、每日容量(MB)。 */
function numberInputs(wrapper: VueWrapper) {
  return wrapper.findAll('.el-input-number-stub')
}

function saveButtons(wrapper: VueWrapper) {
  const buttons = wrapper
    .findAll('.el-button-stub')
    .filter((item) => item.text() === 'ai.common.save')
  return {
    first: buttons[0]!,
    upload: buttons[buttons.length - 1]!
  }
}

beforeEach(() => {
  vi.mocked(userQuota).mockReset()
  vi.mocked(setUserLimit).mockReset()
  vi.mocked(resetUserQuota).mockReset()
  vi.mocked(getUserQuota).mockReset()
  vi.mocked(setUserQuota).mockReset()
  elMessageErrorSpy.mockReset()
  elMessageSuccessSpy.mockReset()
  confirmSpy.mockReset()

  vi.mocked(userQuota).mockResolvedValue(aiFixture)
  vi.mocked(getUserQuota).mockResolvedValue(uploadFixture)
  vi.mocked(setUserQuota).mockResolvedValue(uploadFixture)
  vi.mocked(setUserLimit).mockResolvedValue({
    period: '202610',
    kind: 'af',
    used: 7,
    limit: 800
  })
  vi.mocked(resetUserQuota).mockResolvedValue({
    period: '202610',
    kind: 'af',
    used: 0,
    limit: 500
  })
  confirmSpy.mockResolvedValue('confirm')
})

describe('用户配额对话框', () => {
  it('打开即按 oid 拉两条配额', async () => {
    const wrapper = await mountDialog()

    expect(userQuota).toHaveBeenCalledWith('user-1')
    expect(getUserQuota).toHaveBeenCalledWith('user-1')
    expect(wrapper.find('.el-dialog-stub').text()).toContain('张三')

    wrapper.unmount()
  })

  it('AI 额度展示 used / limit 与来源标签', async () => {
    const wrapper = await mountDialog()

    const text = wrapper.find('.el-dialog-stub').text()
    expect(text).toContain('7 / 500')

    const tags = wrapper.findAll('.el-tag-stub').map((item) => item.text())
    expect(tags).toEqual(['ai.quotaAdmin.sourceExplicit'])
    expect(wrapper.find('.el-tag-stub').attributes('data-type')).toBe('info')

    wrapper.unmount()
  })

  it('覆盖值为 null 时展示实际生效值（继承全局）', async () => {
    const wrapper = await mountDialog()

    expect(wrapper.find('.el-dialog-stub').text()).toContain(
      'ai.userQuota.effective {"files":50,"bytes":"100"}'
    )

    wrapper.unmount()
  })

  it('MB 输入转换成字节提交（32MB → 33554432 字节）', async () => {
    const wrapper = await mountDialog()

    await numberInputs(wrapper)[1]!.setValue('12')
    await numberInputs(wrapper)[2]!.setValue('32')
    await saveButtons(wrapper).upload.trigger('click')
    await flushPromises()

    expect(setUserQuota).toHaveBeenCalledWith('user-1', {
      daily_max_files: 12,
      daily_max_bytes: 32 * 1024 * 1024
    })

    wrapper.unmount()
  })

  it('清空两个输入即提交 null（清除覆盖、继承全局）', async () => {
    const wrapper = await mountDialog()

    await numberInputs(wrapper)[1]!.setValue('')
    await numberInputs(wrapper)[2]!.setValue('')
    await saveButtons(wrapper).upload.trigger('click')
    await flushPromises()

    expect(setUserQuota).toHaveBeenCalledWith('user-1', {
      daily_max_files: null,
      daily_max_bytes: null
    })

    wrapper.unmount()
  })

  it('保存 AI 上限走 PUT 语义的 setUserLimit', async () => {
    const wrapper = await mountDialog()

    await numberInputs(wrapper)[0]!.setValue('800')
    await saveButtons(wrapper).first.trigger('click')
    await flushPromises()

    expect(setUserLimit).toHaveBeenCalledWith('user-1', 'af', 800)

    wrapper.unmount()
  })

  it('重置本月用量需要二次确认', async () => {
    const wrapper = await mountDialog()

    const reset = wrapper
      .findAll('.el-button-stub')
      .find((item) => item.text() === 'ai.quotaAdmin.reset')!
    await reset.trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(resetUserQuota).toHaveBeenCalledWith('user-1', 'af')

    wrapper.unmount()
  })

  it('取消确认时不重置用量', async () => {
    confirmSpy.mockRejectedValueOnce(new Error('cancel'))
    const wrapper = await mountDialog()

    const reset = wrapper
      .findAll('.el-button-stub')
      .find((item) => item.text() === 'ai.quotaAdmin.reset')!
    await reset.trigger('click')
    await flushPromises()

    expect(resetUserQuota).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('附件配额接口缺失时只降级该块，AI 额度仍展示', async () => {
    vi.mocked(getUserQuota).mockRejectedValueOnce(new Error('404'))
    const wrapper = await mountDialog()

    const text = wrapper.find('.el-dialog-stub').text()
    expect(text).toContain('ai.userQuota.loadFailedHint')
    expect(text).toContain('7 / 500')

    wrapper.unmount()
  })
})
