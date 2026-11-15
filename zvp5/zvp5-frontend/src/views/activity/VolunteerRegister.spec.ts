import { defineComponent, h, reactive } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { zhCN } from '@/i18n/locales'
import VolunteerRegister from './VolunteerRegister.vue'

/**
 * 登记编辑器此前被简化成「一次性 insertMany」，三个能力一起丢了：
 *   - 准入判断（ensureRegisterAccess）不再调用；
 *   - URL 的 :mode 没被消费，添加/修改进同一个壳；
 *   - 批量失败只有一个总 toast，没有逐行原因，成功的行也跟着回滚显示。
 *
 * 本组用例按这三条逐项锁死，同时确认修改栏走逐行 PUT、添加栏走批量 insert。
 */

const mocks = vi.hoisted(() => ({
  campus: vi.fn(),
  single: vi.fn(),
  insertMany: vi.fn(),
  readsV3: vi.fn(),
  modifyDuration: vi.fn(),
  ensureRegisterAccess: vi.fn(),
  loadStudentIndex: vi.fn(),
  syncStudentIndex: vi.fn(),
  setHeader: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  confirm: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  route: {
    name: 'activity-register-mode' as string | null,
    params: { mode: 'create' } as Record<string, string>,
    query: { activity: 'act-1' } as Record<string, string>
  }
}))

vi.mock('@/api', () => ({
  default: {
    activity: {
      read: { campus: mocks.campus, single: mocks.single },
      member: { insertMany: mocks.insertMany, reads_v3: mocks.readsV3 },
      duration: { modify: mocks.modifyDuration }
    }
  }
}))

vi.mock('@/api/volunteer', () => ({
  parseVolunteerSheet: vi.fn()
}))

vi.mock('@/api/ai/errors', () => ({
  aiErrorCodeOf: () => null,
  aiErrorCopy: () => ({ title: '', hint: '' })
}))

vi.mock('@/stores/header', () => ({
  useHeaderStore: () => ({ setHeader: mocks.setHeader })
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => reactive({ id: 20240001, _id: 'admin-1', position: ['admin'] })
}))

vi.mock('@/plugins/short-token', () => ({
  temporaryToken: vi.fn(async () => 'temp-token')
}))

vi.mock('./registerAccess', () => ({
  ensureRegisterAccess: mocks.ensureRegisterAccess
}))

vi.mock('@/utils/errorMessage', () => ({
  resolveErrorMessage: (error: unknown) =>
    error instanceof Error ? error.message : String(error ?? '提交失败')
}))

// 本地名单只做「学号精确匹配」，避免把 IndexedDB / axios 链路拉进单测。
vi.mock('@/utils/studentIndex', () => ({
  loadStudentIndex: mocks.loadStudentIndex,
  syncStudentIndex: mocks.syncStudentIndex,
  normalizeStudentId: (value: string) => value.replace(/\D/g, '').slice(0, 12),
  normalizePersonName: (value: string) => value.trim(),
  suggestStudents: () => [],
  matchStudentRow: (
    students: { studentId: string; name: string }[],
    rawId: string,
    rawName: string
  ) => {
    if (!rawId && !rawName) {
      return { status: 'empty', record: null, candidates: [], message: '学号与姓名均为空' }
    }
    const record = students.find((student) => student.studentId === rawId) ?? null
    if (!record) {
      return { status: 'missing', record: null, candidates: [], message: '本地名单未找到该学号' }
    }
    return { status: 'ok', record, candidates: [record], message: '已由学号匹配' }
  }
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace, back: mocks.back }),
  useRoute: () => mocks.route
}))

vi.mock('@/ui/ux/ResultState.vue', () => ({
  default: defineComponent({
    name: 'ResultState',
    props: { ok: Boolean, reason: String, nextText: String, retryText: String },
    setup: (props) => () =>
      h('div', { class: 'result-stub', 'data-ok': String(props.ok) }, props.reason ?? '')
  })
}))

// element-plus 整包 mock：真实包会引入 theme-chalk 的 .css，node ESM 下无法加载。
vi.mock('element-plus', async () => {
  const ElButton = defineComponent({
    name: 'ElButton',
    props: {
      type: String,
      size: String,
      icon: Object,
      loading: Boolean,
      disabled: Boolean,
      text: Boolean,
      circle: Boolean,
      plain: Boolean
    },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          {
            class: 'el-button-stub',
            disabled: props.disabled || props.loading || undefined,
            onClick: () => {
              if (!props.disabled && !props.loading) emit('click')
            }
          },
          slots.default?.()
        )
    }
  })

  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean, title: String, width: String },
    emits: ['update:modelValue'],
    setup(props, { slots }) {
      return () =>
        props.modelValue ? h('div', { class: 'el-dialog-stub' }, slots.default?.()) : null
    }
  })

  const ElSelect = defineComponent({
    name: 'ElSelect',
    props: { modelValue: String, loading: Boolean, placeholder: String, filterable: Boolean },
    emits: ['update:modelValue', 'change'],
    setup: () => () => h('div', { class: 'el-select-stub' })
  })

  const ElOption = defineComponent({
    name: 'ElOption',
    props: { label: String, value: String },
    setup: () => () => null
  })
  const ElTag = defineComponent({
    name: 'ElTag',
    props: { type: String, effect: String },
    setup:
      (_, { slots }) =>
      () =>
        h('span', { class: 'el-tag-stub' }, slots.default?.())
  })
  const ElAlert = defineComponent({
    name: 'ElAlert',
    props: { title: String, description: String },
    setup: (props) => () => h('div', { class: 'el-alert-stub', title: props.title }, props.title)
  })

  return {
    ElButton,
    ElDialog,
    ElSelect,
    ElOption,
    ElTag,
    ElAlert,
    ElMessage: Object.assign(vi.fn(), {
      success: mocks.success,
      warning: mocks.warning,
      error: mocks.error,
      info: mocks.info
    }),
    ElMessageBox: Object.assign(vi.fn(), { confirm: mocks.confirm, alert: vi.fn() })
  }
})

// 后端在事务内抛 HTTPException → 整批回滚；这里构造 axios 形状的错误来区分
// 「明确 4xx（可逐行归因重试）」与「网络/超时/5xx（结果未确认）」。
function httpError(status: number, detail: string) {
  return Object.assign(new Error(detail), {
    isAxiosError: true,
    response: { status, data: { detail } }
  })
}

const ACTIVITY = {
  _id: 'act-1',
  name: '秋季校园清扫',
  date: '2026-10-20',
  status: 'effective',
  type: 'on-campus'
}

const STUDENT_A = {
  oid: 'oid-a',
  studentId: '20240001',
  name: '张三',
  classId: 'class-1',
  className: '高一(1)班'
}

const STUDENT_B = {
  oid: 'oid-b',
  studentId: '20240002',
  name: '李四',
  classId: 'class-1',
  className: '高一(1)班'
}

function buildI18n() {
  return createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN } })
}

const mounted: VueWrapper[] = []

async function mountEditor(mode: 'create' | 'edit'): Promise<VueWrapper> {
  mocks.route.params = { mode }
  const wrapper = mount(VolunteerRegister, { global: { plugins: [buildI18n()] } })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

async function fillRow(wrapper: VueWrapper, index: number, studentId: string, duration: string) {
  await wrapper.get(`[data-cell="${index}-0"]`).setValue(studentId)
  await wrapper.get(`[data-cell="${index}-2"]`).setValue(duration)
  await flushPromises()
}

function submitButton(wrapper: VueWrapper) {
  return wrapper.findAll('.el-button-stub').find((button) => button.text().startsWith('提交'))
}

function resultText(wrapper: VueWrapper): string {
  return wrapper.find('.result-stub').exists() ? wrapper.get('.result-stub').text() : ''
}

beforeEach(() => {
  localStorage.clear()
  mocks.campus.mockReset().mockResolvedValue({ activities: [ACTIVITY] })
  mocks.single.mockReset().mockResolvedValue({ activity: ACTIVITY })
  mocks.insertMany.mockReset().mockResolvedValue(undefined)
  mocks.readsV3.mockReset().mockResolvedValue({ members: [], total: 0 })
  mocks.modifyDuration.mockReset().mockResolvedValue(undefined)
  mocks.ensureRegisterAccess.mockReset().mockResolvedValue(true)
  mocks.loadStudentIndex.mockReset().mockResolvedValue({
    students: [STUDENT_A, STUDENT_B],
    updatedAt: Date.now(),
    source: 'index.db'
  })
  mocks.syncStudentIndex.mockReset().mockResolvedValue({
    students: [STUDENT_A, STUDENT_B],
    updatedAt: Date.now(),
    source: 'network'
  })
  for (const spy of [
    mocks.setHeader,
    mocks.success,
    mocks.warning,
    mocks.error,
    mocks.info,
    mocks.confirm,
    mocks.push,
    mocks.replace,
    mocks.back
  ]) {
    spy.mockReset()
  }
  mocks.confirm.mockResolvedValue('confirm')
})

afterEach(() => {
  while (mounted.length > 0) {
    mounted.pop()?.unmount()
  }
  localStorage.clear()
})

describe('VolunteerRegister 准入与栏目', () => {
  it('进入编辑器先走统一的准入判断', async () => {
    await mountEditor('create')

    expect(mocks.ensureRegisterAccess).toHaveBeenCalledTimes(1)
  })

  it('准入不通过就不再加载名单与活动', async () => {
    mocks.ensureRegisterAccess.mockResolvedValue(false)

    await mountEditor('edit')

    expect(mocks.ensureRegisterAccess).toHaveBeenCalledTimes(1)
    expect(mocks.loadStudentIndex).not.toHaveBeenCalled()
    expect(mocks.campus).not.toHaveBeenCalled()
    expect(mocks.readsV3).not.toHaveBeenCalled()
  })

  it('按 URL 的 :mode 切标题与提交语义', async () => {
    const createWrapper = await mountEditor('create')
    expect(createWrapper.get('.yg2-page-header__title').text()).toBe(
      zhCN.nav.registerEntry.create.title
    )
    expect(createWrapper.text()).toContain('添加义工')
    expect(submitButton(createWrapper)!.text()).toBe('提交登记')
    createWrapper.unmount()
    mounted.pop()

    const editWrapper = await mountEditor('edit')
    expect(editWrapper.get('.yg2-page-header__title').text()).toBe(
      zhCN.nav.registerEntry.edit.title
    )
    expect(editWrapper.text()).toContain('修改义工')
    expect(submitButton(editWrapper)!.text()).toBe('提交修改')
    // 标题同步写进顶栏，浏览器写作「修改义工」而不是笼统的「义工登记」。
    expect(mocks.setHeader).toHaveBeenCalledWith(zhCN.nav.registerEntry.edit.title)
  })
})

describe('VolunteerRegister 批量提交', () => {
  it('新增成功时按人计数并给出成功结果', async () => {
    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mocks.insertMany).toHaveBeenCalledTimes(1)
    expect(mocks.insertMany.mock.calls[0][0]).toBe('act-1')
    expect(mocks.insertMany.mock.calls[0][1]).toEqual([
      { member: 'oid-a', mode: 'on-campus', duration: 3 }
    ])
    expect(resultText(wrapper)).toContain('登记 1 人')
    expect(wrapper.text()).toContain('查看活动（1 人）')
  })

  it('添加模式一律带 reject_existing，禁止默认累加', async () => {
    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    const options = mocks.insertMany.mock.calls[0][2]
    expect(options).toMatchObject({ rejectExisting: true, timeout: 60000 })
    // 绝不下发 overwrite:true（那是「设置时长」语义，属于修改栏）。
    expect(options?.overwrite).toBeFalsy()
  })

  it('后端明确 400 回滚后逐行归因：成功行保留、失败行写明原因', async () => {
    // 400 由后端在事务内抛出 → 整批回滚，逐行重试才能知道该怪谁。
    mocks.insertMany.mockImplementation((_aid: string, payload: { member: string }[]) => {
      if (payload.length > 1) return Promise.reject(httpError(400, '批量写入失败'))
      return payload[0].member === 'oid-a'
        ? Promise.resolve()
        : Promise.reject(httpError(400, '成员档案缺失'))
    })

    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')
    await fillRow(wrapper, 1, '20240002', '4')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    const summary = zhCN.activity.register.partialFailedSummary
      .replace('{updated}', '0')
      .replace('{added}', '1')
      .replace('{failed}', '1')
    expect(mocks.warning).toHaveBeenCalledWith(summary)

    // 汇总 + 逐行原因都进结果面板：既有全局数字，也有「谁、为什么」。
    const result = resultText(wrapper)
    expect(result).toContain(summary)
    expect(result).toContain('李四（20240002）：成员档案缺失')

    // 成功的行留在「已提交」，失败的行带着自己的原因，不会一起回滚。
    expect(wrapper.text()).toContain('已新增')
    expect(wrapper.text()).toContain('提交失败：成员档案缺失')
    expect(wrapper.text()).toContain('查看活动（1 人）')
    // 三次写入都锁住 reject_existing，逐行重试也不会退化成累加。
    expect(mocks.insertMany).toHaveBeenCalledTimes(3)
    for (const call of mocks.insertMany.mock.calls) {
      expect(call[2]).toMatchObject({ rejectExisting: true })
    }
  })

  it('批量超时不得触发单行 POST，也不计成功', async () => {
    // 没有 response 的 axios 错误 = 请求可能已在服务端提交后才断：不能重试。
    mocks.insertMany.mockRejectedValue(new Error('timeout of 60000ms exceeded'))

    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')
    await fillRow(wrapper, 1, '20240002', '4')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    // 只发出那一次整批请求，绝没有逐行补发。
    expect(mocks.insertMany).toHaveBeenCalledTimes(1)
    expect(mocks.insertMany.mock.calls[0][1]).toHaveLength(2)

    expect(resultText(wrapper)).toContain(
      zhCN.activity.register.submitUnconfirmedSummary.replace('{count}', '2')
    )
    expect(wrapper.text()).toContain(zhCN.activity.register.submitUnconfirmed)
    expect(wrapper.text()).not.toContain('已新增')
    expect(wrapper.text()).not.toContain('查看活动（')
    // 未确认的行不能显示成「提交失败」，否则会被当成可安全重试。
    expect(wrapper.text()).not.toContain('提交失败：')
  })

  it('5xx 同样只报未确认，不重试', async () => {
    mocks.insertMany.mockRejectedValue(httpError(500, '服务暂不可用'))

    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mocks.insertMany).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain(zhCN.activity.register.submitUnconfirmed)
  })

  it('结果未确认且索引还没刷新回来时，再点提交也不会重发', async () => {
    mocks.insertMany.mockRejectedValue(new Error('timeout of 60000ms exceeded'))

    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')

    // 自动重拉索引挂起：模拟「核对请求还没回来」的窗口期。
    let release: () => void = () => {}
    mocks.readsV3.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ members: [], total: 0 })
      })
    )

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()
    expect(mocks.insertMany).toHaveBeenCalledTimes(1)

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    // 仍在窗口期：不允许多发一次，否则已提交的那批会翻倍。
    expect(mocks.insertMany).toHaveBeenCalledTimes(1)
    expect(mocks.warning).toHaveBeenLastCalledWith(zhCN.activity.register.submitUnconfirmed)

    release()
    await flushPromises()
  })

  it('409 冲突逐行归因但不重复累加，也不谎报已提交人数', async () => {
    const conflict = () => httpError(409, '已有 1 条活动记录，确认后可按本次时长覆盖')
    mocks.insertMany.mockImplementation((_aid: string, payload: { member: string }[]) =>
      Promise.reject(
        payload.length > 1
          ? httpError(409, '已有 2 条活动记录，确认后可按本次时长覆盖')
          : conflict()
      )
    )

    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')
    await fillRow(wrapper, 1, '20240002', '4')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    // 整批 1 次 + 逐行 2 次；一次都没有 overwrite，时长不会被再累加一遍。
    expect(mocks.insertMany).toHaveBeenCalledTimes(3)
    for (const call of mocks.insertMany.mock.calls) {
      expect(call[2]).toMatchObject({ rejectExisting: true })
      expect(call[2]?.overwrite).toBeFalsy()
    }

    expect(wrapper.text()).not.toContain('查看活动（')
    expect(wrapper.text()).toContain('提交失败：已有 1 条活动记录')
    expect(resultText(wrapper)).toContain('失败 2 条')
    expect(wrapper.get('.result-stub').attributes('data-ok')).toBe('false')
  })
})

describe('VolunteerRegister 修改栏', () => {
  beforeEach(() => {
    mocks.readsV3.mockResolvedValue({
      members: [
        {
          _id: 'sess-a',
          member: 'oid-a',
          activity: 'act-1',
          status: 'effective',
          mode: 'on-campus',
          duration: 2
        }
      ],
      total: 1
    })
  })

  it('已录入成员标记「已录入」，提交走逐行 PUT 覆盖时长', async () => {
    const wrapper = await mountEditor('edit')
    await fillRow(wrapper, 0, '20240001', '3')

    expect(wrapper.text()).toContain('已录入')

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mocks.insertMany).not.toHaveBeenCalled()
    expect(mocks.modifyDuration).toHaveBeenCalledWith('sess-a', 'act-1', 3, 'on-campus', {
      silent: true,
      timeout: 60000
    })
    expect(resultText(wrapper)).toContain('覆盖 1 人')
  })

  it('不在活动里的成员在修改栏被拦下，不会悄悄新增', async () => {
    mocks.readsV3.mockResolvedValue({ members: [], total: 0 })

    const wrapper = await mountEditor('edit')
    await fillRow(wrapper, 0, '20240002', '3')

    expect(wrapper.text()).toContain(zhCN.activity.register.notInActivity)
    expect(submitButton(wrapper)!.attributes('disabled')).toBeDefined()

    await submitButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(mocks.modifyDuration).not.toHaveBeenCalled()
    expect(mocks.insertMany).not.toHaveBeenCalled()
  })

  it('已在活动中的成员在添加栏被挡下，提示去修改栏', async () => {
    const wrapper = await mountEditor('create')
    await fillRow(wrapper, 0, '20240001', '3')

    expect(wrapper.text()).toContain('该成员已在活动中')
    expect(submitButton(wrapper)!.attributes('disabled')).toBeDefined()
  })
})
