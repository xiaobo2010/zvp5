import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h } from 'vue'
import ZLogCard from './ZLogCard.vue'
import type { Log } from '../../../types/log'

// element-plus 整包 mock:真实包会连带引入 theme-chalk 的 .css,node ESM 下无法加载
// (同 ZBanRules.spec.ts 的处理)。
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const ElFormItem = defineComponent({
    name: 'ElFormItem',
    props: { label: String },
    setup(props, { slots }) {
      return () => h('div', { class: 'el-form-item-stub', 'data-label': props.label }, slots.default?.())
    }
  })
  return {
    ElCard: { name: 'ElCard', template: '<section><slot /></section>' },
    ElForm: { name: 'ElForm', template: '<div class="el-form-stub"><slot /></div>' },
    ElFormItem,
    ElTag: {
      name: 'ElTag',
      props: { type: String, size: String },
      setup(_, { slots }) {
        return () => h('span', { class: 'el-tag-stub' }, slots.default?.())
      }
    }
  }
})

// 时间渲染不是本组用例的关注点,直接钉成固定串避免依赖真实 dayjs 时区行为
vi.mock('@/plugins/dayjs', () => ({
  default: {
    unix: () => ({ format: () => '2026-10-21 12:00:00' })
  }
}))

function logFixture(overrides: Partial<Log> = {}): Log {
  return {
    _id: 'log-1',
    url: '/api/v3/ban/rules',
    user: '',
    data: '',
    timestamp: 1761024000,
    ...overrides
  }
}

function mountCard(log: Log): VueWrapper {
  return mount(ZLogCard, { props: { log } })
}

beforeEach(() => {})

afterEach(() => {
  vi.restoreAllMocks()
})

it('shows the resolved user name and student id instead of the raw oid', () => {
  const wrapper = mountCard(
    logFixture({ user: '69b7a4ad0243d973f1e1a2b3', user_name: '张三', user_id: '20240801' })
  )

  expect(wrapper.find('.el-form-item-stub[data-label="用户"]').text()).toContain('张三')
  expect(wrapper.find('.el-form-item-stub[data-label="用户"]').text()).toContain('20240801')
  expect(wrapper.text()).not.toContain('69b7a4ad0243d973f1e1a2b3')

  wrapper.unmount()
})

it('falls back to 未知用户 when user_name is missing but user is set', () => {
  const wrapper = mountCard(
    logFixture({ user: '69b7a4ad0243d973f1e1a2b3', user_name: null as unknown as string })
  )

  expect(wrapper.text()).toContain('未知用户')
  expect(wrapper.text()).not.toContain('69b7a4ad0243d973f1e1a2b3')

  wrapper.unmount()
})

it('shows 匿名 when both user and user_name are empty', () => {
  const wrapper = mountCard(logFixture())

  expect(wrapper.find('.el-form-item-stub[data-label="用户"]').text()).toContain('匿名')

  wrapper.unmount()
})

it('renders system_ssh_command as a monospace operator and command line', () => {
  const wrapper = mountCard(
    logFixture({
      user: '69b7a4ad0243d973f1e1a2b3',
      user_name: '张三',
      type: 'system_ssh_command',
      data: JSON.stringify({
        command: 'ls -la',
        operator: '69b7a4ad0243d973f1e1a2b3',
        operator_name: '张三'
      })
    })
  )

  const detail = wrapper.find('.el-form-item-stub[data-label="详情"]')
  expect(detail.text()).toContain('张三 $ ls -la')
  expect(detail.text()).not.toContain('69b7a4ad0243d973f1e1a2b3')
  expect(detail.find('.font-mono').exists()).toBe(true)

  wrapper.unmount()
})

it('renders dual auth details with operator and approver names', () => {
  const wrapper = mountCard(
    logFixture({
      type: 'system_dual_auth',
      data: JSON.stringify({
        action: 'web_ssh',
        operator: '69b7a4ad0243d973f1e1a2b3',
        operator_name: '张三',
        approver: '69b7a4ad0243d973f1e1a2b4',
        approver_name: '李四',
        reason: '临时排查磁盘'
      })
    })
  )

  expect(wrapper.find('.el-form-item-stub[data-label="详情"]').text()).toContain(
    'Web SSH · 操作人 张三 · 审批人 李四'
  )
  expect(wrapper.find('.el-form-item-stub[data-label="详情"]').text()).toContain('原因：临时排查磁盘')

  wrapper.unmount()
})

it('keeps raw content and does not crash when audit data is broken JSON', () => {
  const wrapper = mountCard(logFixture({ type: 'system_op', data: '{"action":"files",oops' }))

  expect(wrapper.find('.el-form-item-stub[data-label="详情"]').text()).toBe('{"action":"files",oops')
  expect(wrapper.find('.el-form-item-stub[data-label="详情"]').text()).not.toContain('未知用户')

  wrapper.unmount()
})

it('keeps raw content for unknown log types', () => {
  const wrapper = mountCard(
    logFixture({
      type: 'user_login',
      data: '登录成功'
    })
  )

  expect(wrapper.find('.el-form-item-stub[data-label="详情"]').text()).toBe('登录成功')

  wrapper.unmount()
})

// 已知类型的封面:typeLabels 命中后应显示中文,而不是透出英文原始值
it('translates known log types like ban_change into Chinese', () => {
  const wrapper = mountCard(logFixture({ type: 'ban_change', data: '' }))

  expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).toContain('封禁变更')
  expect(wrapper.text()).not.toContain('ban_change')

  wrapper.unmount()
})

// 未知类型回落原值,不出「未知」也不猜
it('falls back to the raw type for types without a label', () => {
  const futureType = 'future_new_type'

  const wrapper = mountCard(logFixture({ type: futureType, data: '' }))

  expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).toContain(futureType)
  expect(wrapper.text()).not.toContain('未知')

  wrapper.unmount()
})
