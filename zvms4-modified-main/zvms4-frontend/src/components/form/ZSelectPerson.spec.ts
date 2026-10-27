import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZSelectPerson from './ZSelectPerson.vue'
import api from '@/api'

vi.mock('@/api', () => ({
  default: {
    user: {
      read: vi.fn(),
      readOne: vi.fn()
    },
    group: {
      readOne: vi.fn()
    }
  }
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      key === 'ai.select.needMoreDigits' ? `${key} ${String(params?.n)}` : key
  })
}))

const read = vi.mocked(api.user.read)
const readOne = vi.mocked(api.user.readOne)
const readOneGroup = vi.mocked(api.group.readOne)

function makeUser(id: number, name: string, oid: string) {
  return { _id: oid, id, name, group: ['group-1'], past: [] }
}

function optionTexts() {
  return Array.from(document.querySelectorAll('.el-select-dropdown__item')).map((item) =>
    item.textContent?.trim()
  )
}

function emptyHint() {
  return document.querySelector('.yg-select-person__hint')?.textContent?.trim()
}

async function waitForFilter() {
  await new Promise((resolve) => setTimeout(resolve, 350))
  await flushPromises()
}

let wrappers: VueWrapper[] = []

function mountSelect(props: Record<string, unknown> = {}, attrs: Record<string, unknown> = {}) {
  const wrapper = mount(ZSelectPerson, {
    props: { modelValue: '', filterStart: 6, ...props },
    attrs,
    attachTo: document.body
  })
  wrappers.push(wrapper)
  return wrapper
}

function selectedLabel() {
  return document.querySelector('.el-select__placeholder span')?.textContent?.trim()
}

beforeEach(() => {
  read.mockReset()
  readOne.mockReset()
  readOneGroup.mockReset()
  readOneGroup.mockResolvedValue({ _id: 'group-1', type: 'class', name: '高一 1 班' })
})

afterEach(() => {
  for (const wrapper of wrappers) {
    wrapper.unmount()
  }
  wrappers = []
  document.body.innerHTML = ''
})

describe('ZSelectPerson', () => {
  it('按当前输入关键词搜索，而不是只看已选值', async () => {
    read.mockResolvedValue({ users: [makeUser(20210001, '张三', 'u1')], size: 1 })
    const wrapper = mountSelect()

    await wrapper.find('input').setValue('张三')
    await waitForFilter()

    expect(read).toHaveBeenCalledWith('张三', 1, 5)
    expect(optionTexts()).toEqual(['张三20210001, 高一 1 班'])
  })

  it('选中一人后仍能按新关键词改选', async () => {
    read.mockImplementation(async (query?: string) =>
      query === '张三'
        ? { users: [makeUser(20210001, '张三', 'u1')], size: 1 }
        : { users: [makeUser(20210002, '李四', 'u2')], size: 1 }
    )
    const wrapper = mountSelect()

    await wrapper.find('input').setValue('张三')
    await waitForFilter()
    document
      .querySelectorAll('.el-select-dropdown__item')[0]
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['u1'])

    await wrapper.setProps({ modelValue: 'u1' })
    await wrapper.find('input').setValue('李四')
    await waitForFilter()

    expect(read).toHaveBeenLastCalledWith('李四', 1, 5)
    expect(optionTexts()).toEqual(['李四20210002, 高一 1 班'])
  })

  it('数字不足门槛时给提示而不是静默空列表', async () => {
    const wrapper = mountSelect()

    await wrapper.find('input').setValue('123')
    await waitForFilter()

    expect(read).not.toHaveBeenCalled()
    expect(optionTexts()).toEqual([])
    expect(emptyHint()).toBe('ai.select.needMoreDigits 6')
  })

  it('中文不足两字时给提示', async () => {
    const wrapper = mountSelect()

    await wrapper.find('input').setValue('张')
    await waitForFilter()

    expect(read).not.toHaveBeenCalled()
    expect(emptyHint()).toBe('ai.select.needMoreName')
  })

  it('搜索失败时结束 loading 且可重试', async () => {
    read.mockRejectedValueOnce(new Error('boom'))
    read.mockResolvedValueOnce({ users: [makeUser(20210001, '张三', 'u1')], size: 1 })
    const wrapper = mountSelect({ filterStart: 2 })

    await wrapper.find('input').setValue('张三')
    await waitForFilter()

    expect(emptyHint()).toBe('ai.select.searchFailed')
    expect(document.querySelector('.el-select-dropdown__loading')).toBeNull()

    await wrapper.find('input').setValue('张')
    await waitForFilter()
    await wrapper.find('input').setValue('张三')
    await waitForFilter()

    expect(optionTexts()).toEqual(['张三20210001, 高一 1 班'])
  })

  it('关键词清空时不发请求，只展示当前已选项', async () => {
    read.mockImplementation(async (query?: string) =>
      query
        ? { users: [makeUser(20210001, '张三', 'u1')], size: 1 }
        : { users: [], size: 0 }
    )
    readOne.mockResolvedValue(makeUser(20210001, '张三', 'u1'))
    const wrapper = mountSelect({ modelValue: 'u1' })
    await flushPromises()
    expect(readOne).toHaveBeenCalledWith('u1')

    await wrapper.find('input').setValue('张三')
    await waitForFilter()
    const calls = read.mock.calls.length

    await wrapper.find('input').setValue('')
    await waitForFilter()

    expect(read.mock.calls.length).toBe(calls)
    expect(optionTexts()).toEqual(['张三20210001, 高一 1 班'])
  })

  it('外部清空/换值时内部选择跟着同步', async () => {
    readOne.mockResolvedValue(makeUser(20210003, '王五', 'u3'))
    const wrapper = mountSelect({ modelValue: 'u3' })
    await flushPromises()

    expect(selectedLabel()).toBe('王五')

    await wrapper.setProps({ modelValue: '' })
    await flushPromises()
    expect(selectedLabel()).not.toBe('王五')

    await wrapper.setProps({ modelValue: 'u3' })
    await flushPromises()
    expect(selectedLabel()).toBe('王五')
  })

  it('clearable 清空时 emit 空字符串', async () => {
    readOne.mockResolvedValue(makeUser(20210003, '王五', 'u3'))
    const wrapper = mountSelect({ modelValue: 'u3' }, { clearable: true })
    await flushPromises()

    await wrapper.trigger('pointerenter')
    await wrapper.trigger('mouseenter')
    await flushPromises()
    await wrapper.find('.el-select__clear').trigger('click')
    await flushPromises()

    const updated = wrapper.emitted('update:modelValue')
    const selected = wrapper.emitted('select')
    expect(updated?.[updated.length - 1]).toEqual([''])
    expect(selected?.[selected.length - 1]).toEqual([null])
  })
})
