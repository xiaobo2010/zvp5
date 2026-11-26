import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h, type SetupContext } from 'vue'
import ZLogCard from './ZLogCard.vue'
import i18n from '@/i18n'
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
      setup(_props: unknown, { slots }: SetupContext) {
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
    url: '/api/v1/ban/rules',
    user: '',
    data: '',
    timestamp: 1761024000,
    ...overrides
  }
}

function mountCard(log: Log): VueWrapper {
  // login_fail 等标签走 i18n，挂真实 locale 才能断言中文文案
  return mount(ZLogCard, { props: { log }, global: { plugins: [i18n] } })
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

// 附件各写入点拆分后,每个细类型都要命中自己的中文标签,不能退化成「附件」
it('labels each attachment log type separately', () => {
  const labels: Record<string, string> = {
    attachment_view: '附件查看',
    attachment_audit: '附件审计',
    attachment_upload: '附件上传',
    attachment_status: '附件裁决',
    attachment_link: '附件外链',
    attachment_verify: '附件查证',
    attachment_bind: '附件绑定',
    attachment_delete: '附件删除'
  }

  for (const [type, label] of Object.entries(labels)) {
    const wrapper = mountCard(logFixture({ type }))
    expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).toContain(label)
    wrapper.unmount()
  }
})

// 风险监控与定时任务写日志时不经过请求上下文,漏标签同样会在页面上透出英文
it('labels risk monitor and scheduled job log types', () => {
  const labels: Record<string, string> = {
    risk_alert: '风险告警',
    risk_ban: '风险封禁',
    retention_purge: '留存清理',
    user_trash_gc: '用户回收站清理',
    adb_backup: 'ADB 备份',
    ai_governance: 'AI 治理',
    ai_ops_extra: 'AI 运维操作'
  }

  for (const [type, label] of Object.entries(labels)) {
    const wrapper = mountCard(logFixture({ type }))
    expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).toContain(label)
    wrapper.unmount()
  }
})

// 未知类型回落原值,不出「未知」也不猜
it('falls back to the raw type for types without a label', () => {
  const futureType = 'future_new_type'

  const wrapper = mountCard(logFixture({ type: futureType, data: '' }))

  expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).toContain(futureType)
  expect(wrapper.text()).not.toContain('未知')

  wrapper.unmount()
})

// 后端实际会写的类型都必须有中文标签:漏一个就在页面上透出英文原始值
it('labels every event type the backend actually writes', () => {
  const realTypes = [
    'user_login',
    'user_logout',
    'user_verify',
    'login_fail',
    'device_required',
    'device_ban_skipped',
    'ban_rule_skipped',
    'system_settings_update',
    'system_user_quota_update',
    'system_ssh_suggest',
    'system_db_read',
    'system_db_query',
    'notification_rollback',
    'attachment_ocr_enqueue',
    'attachment_view',
    'attachment_audit',
    'attachment_upload',
    'attachment_status',
    'attachment_link',
    'attachment_verify',
    'attachment_bind',
    'attachment_delete',
    'risk_alert',
    'risk_ban',
    'retention_purge',
    'user_trash_gc',
    'adb_backup',
    'ai_governance',
    'ai_ops_extra',
    'ai_quota_set',
    'ai_quota_reset',
    'ai_quota_request_approve',
    'ai_quota_request_reject',
    'ai_api_key_delete',
    'ai_prompt_access',
    'agent_jailbreak_warning',
    'platform_misuse_report',
    'activity_member_review'
  ]

  for (const type of realTypes) {
    const wrapper = mountCard(logFixture({ type }))
    expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).not.toContain(type)
    wrapper.unmount()
  }
})

// 幻影键(后端从未写入的类型)不该再占着 label map
it('does not keep labels for log types no code path writes', () => {
  for (const phantom of ['system_db_content', 'system_files', 'otp']) {
    const wrapper = mountCard(logFixture({ type: phantom }))
    expect(wrapper.find('.el-form-item-stub[data-label="类型"]').text()).toContain(phantom)
    wrapper.unmount()
  }
})

it('shows client ip and user agent when the log carries them', () => {
  const wrapper = mountCard(
    logFixture({
      type: 'user_login',
      data: '登录成功',
      client_ip: '203.0.113.7',
      user_agent: 'Mozilla/5.0 (X11; Linux x86_64)'
    } as Partial<Log>)
  )

  expect(wrapper.find('.el-form-item-stub[data-label="IP"]').text()).toContain('203.0.113.7')
  expect(wrapper.find('.el-form-item-stub[data-label="UA"]').text()).toContain('Mozilla/5.0')

  wrapper.unmount()
})

it('hides the ip and user agent rows for logs written before the columns existed', () => {
  const wrapper = mountCard(logFixture({ type: 'user_login', data: '登录成功' }))

  expect(wrapper.find('.el-form-item-stub[data-label="IP"]').exists()).toBe(false)
  expect(wrapper.find('.el-form-item-stub[data-label="UA"]').exists()).toBe(false)

  wrapper.unmount()
})
