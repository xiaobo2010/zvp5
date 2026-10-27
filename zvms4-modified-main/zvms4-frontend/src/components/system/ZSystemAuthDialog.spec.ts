import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { PropType } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ZSystemAuthDialog from './ZSystemAuthDialog.vue'

/**
 * 双人授权弹窗。
 *
 * 守住的是「调用方视角」的契约：open() 返回 Promise、submit 后弹层不自动关、
 * 失败由父级 fail() 回填错误并保留弹层、取消 resolve(null)。视觉上的输入规格
 * （只留数字、满 6 位自动提交、回车提交、loading 期间禁用）一并锁在这里。
 */

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
  const { computed, defineComponent, h, nextTick, ref, watch } = await import('vue')

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, width: String },
    emits: ['update:modelValue', 'opened'],
    setup(props, { emit, slots }) {
      watch(
        () => props.modelValue,
        (value) => {
          if (value) {
            void nextTick(() => emit('opened'))
          }
        }
      )
      return () =>
        props.modelValue
          ? h('div', { class: 'el-dialog-stub', 'data-width': props.width }, [
              h(
                'header',
                { class: 'el-dialog-stub__header' },
                slots.header?.({ titleId: 'z-auth-title', close: () => emit('update:modelValue', false) })
              ),
              h('div', { class: 'el-dialog-stub__body' }, slots.default?.()),
              h('div', { class: 'el-dialog-stub__footer' }, slots.footer?.())
            ])
          : null
    }
  })

  const ElInput = defineComponent({
    name: 'ElInput',
    props: {
      modelValue: { type: String, default: '' },
      formatter: {
        type: Function as PropType<(value: string) => string>,
        default: undefined
      },
      parser: {
        type: Function as PropType<(value: string) => string>,
        default: undefined
      },
      disabled: Boolean
    },
    emits: ['update:modelValue', 'input'],
    setup(props, { emit, expose }) {
      const element = ref<HTMLInputElement | null>(null)
      expose({
        focus: () => element.value?.focus(),
        select: () => element.value?.select()
      })
      const display = computed(() =>
        props.formatter ? props.formatter(props.modelValue) : props.modelValue
      )
      return () =>
        h('input', {
          ref: element,
          class: 'el-input-stub',
          value: display.value,
          disabled: props.disabled,
          onInput: (event: Event) => {
            const raw = (event.target as HTMLInputElement).value
            const value = props.parser ? props.parser(raw) : raw
            emit('update:modelValue', value)
            emit('input', value)
          }
        })
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
            'data-type': props.type ?? '',
            'data-loading': props.loading ? 'true' : 'false',
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

  return { ElDialog, ElInput, ElButton }
})

interface DialogApi {
  open: (target: string) => Promise<string | null>
  succeed: () => void
  fail: (message: string) => void
  cancel: () => void
}

const wrappers: VueWrapper[] = []

function mountDialog(): VueWrapper {
  const wrapper = mount(ZSystemAuthDialog, { attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}

function api(wrapper: VueWrapper): DialogApi {
  return wrapper.vm as unknown as DialogApi
}

function codeInput(wrapper: VueWrapper) {
  return wrapper.find('.el-input-stub')
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('.el-button-stub').find((item) => item.text() === text)
  if (!button) {
    throw new Error(`button not found: ${text}`)
  }
  return button
}

afterEach(() => {
  while (wrappers.length > 0) {
    wrappers.pop()?.unmount()
  }
})

describe('ZSystemAuthDialog', () => {
  it('open 返回 Promise 并渲染目标操作与 6 位输入规格', async () => {
    const wrapper = mountDialog()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)

    const pending = api(wrapper).open('删除 /srv/app/config.yml')
    await flushPromises()

    expect(pending).toBeInstanceOf(Promise)
    expect(wrapper.find('.el-dialog-stub').text()).toContain('删除 /srv/app/config.yml')
    expect(wrapper.find('.el-dialog-stub').attributes('data-width')).toBe(
      'min(420px, calc(100vw - 32px))'
    )
    // 标题通过 header 插槽的 titleId 与 aria-labelledby 对接
    expect(wrapper.find('.el-dialog-stub__header h2').attributes('id')).toBe('z-auth-title')

    const input = codeInput(wrapper)
    expect(input.attributes('maxlength')).toBe('6')
    expect(input.attributes('inputmode')).toBe('numeric')
    expect(input.attributes('autocomplete')).toBe('one-time-code')
  })

  it('输入满 6 位自动提交并 resolve 动态码', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')

    await flushPromises()
    await codeInput(wrapper).setValue('123456')
    await flushPromises()

    await expect(pending).resolves.toBe('123456')
    // resolve 后弹层不自己关：由父级 succeed()/fail() 决定
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
  })

  it('非数字被过滤，只剩 6 位数字', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')

    await flushPromises()
    await codeInput(wrapper).setValue('12a34-567')
    await flushPromises()

    expect((codeInput(wrapper).element as HTMLInputElement).value).toBe('123456')
    await expect(pending).resolves.toBe('123456')
  })

  it('回车提交：不足 6 位给出格式错误', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')
    await flushPromises()

    await codeInput(wrapper).setValue('123')
    await codeInput(wrapper).trigger('keyup.enter')
    await flushPromises()

    expect(wrapper.find('.auth-error').text()).toBe('server.auth.formatInvalid')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    expect(wrapper.find('.el-button-stub[data-type="primary"]').attributes('disabled')).toBeUndefined()

    api(wrapper).cancel()
    await flushPromises()
    await expect(pending).resolves.toBeNull()
  })

  it('回车同样送交校验：满 6 位时 code 被 resolve', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')
    await flushPromises()

    await codeInput(wrapper).setValue('654321')
    await codeInput(wrapper).trigger('keyup.enter')
    await flushPromises()

    await expect(pending).resolves.toBe('654321')
  })

  it('父级 fail 后保留弹层并显示错误，输入框可用', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')
    await flushPromises()

    await codeInput(wrapper).setValue('123456')
    await flushPromises()
    await expect(pending).resolves.toBe('123456')

    api(wrapper).fail('动态码无效或已过期')
    await flushPromises()

    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
    expect(wrapper.find('.auth-error').text()).toBe('动态码无效或已过期')
    expect(wrapper.find('.auth-error').attributes('aria-live')).toBe('polite')
    expect(codeInput(wrapper).attributes('disabled')).toBeUndefined()
    expect(buttonByText(wrapper, 'server.auth.confirm').attributes('disabled')).toBeUndefined()
  })

  it('提交后到父级回话之间：两个按钮禁用并显示 loading', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')
    await flushPromises()

    await codeInput(wrapper).setValue('123456')
    await flushPromises()
    await expect(pending).resolves.toBe('123456')

    const cancelButton = buttonByText(wrapper, 'server.auth.cancel')
    const confirmButton = buttonByText(wrapper, 'server.auth.verifying')
    expect(cancelButton.attributes('disabled')).toBeDefined()
    expect(confirmButton.attributes('disabled')).toBeDefined()
    expect(confirmButton.attributes('data-loading')).toBe('true')
    // 禁用期间重复点击不会二次提交
    await confirmButton.trigger('click')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(true)
  })

  it('取消 resolve(null) 并关闭弹层', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')
    await flushPromises()

    await buttonByText(wrapper, 'server.auth.cancel').trigger('click')
    await flushPromises()

    await expect(pending).resolves.toBeNull()
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  })

  it('succeed 用最后提交的码 resolve 并关闭弹层', async () => {
    const wrapper = mountDialog()
    const pending = api(wrapper).open('删除 /srv/app')
    await flushPromises()

    await codeInput(wrapper).setValue('123456')
    await flushPromises()
    api(wrapper).succeed()
    await flushPromises()

    await expect(pending).resolves.toBe('123456')
    expect(wrapper.find('.el-dialog-stub').exists()).toBe(false)
  })
})
