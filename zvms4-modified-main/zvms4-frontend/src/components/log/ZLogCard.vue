<script setup lang="ts">
import type { Log } from '../../../types/log'
import { computed, toRefs } from 'vue'
import { ElCard, ElForm, ElFormItem, ElTag } from 'element-plus'
import dayjs from '@/plugins/dayjs'

const props = defineProps<{
  log: Log
}>()

const { log } = toRefs(props)

type LogWithClient = Log & { client_ip?: string | null; user_agent?: string | null }

const clientIp = computed(() => (log.value as LogWithClient).client_ip ?? '')
const userAgent = computed(() => (log.value as LogWithClient).user_agent ?? '')

const typeLabels: Record<string, string> = {
  user_login: '登录',
  user_logout: '登出',
  user_verify: '身份验证',
  login_fail: '登录失败',
  passwd_change: '密码修改',
  totp_view: '动态码查看',
  user_create: '用户创建',
  user_hard_delete: '用户彻底删除',
  role_change: '角色变更',
  past_change: '历史身份变更',
  student_id_change: '学号变更',
  group_create: '班级创建',
  group_update: '班级修改',
  group_delete: '班级删除',
  group_member_add: '班级成员加入',
  group_member_remove: '班级成员移出',
  activity_create: '义工创建',
  activity_update: '义工修改',
  activity_delete: '义工删除',
  activity_member_add: '义工成员添加',
  activity_member_update: '义工成员修改',
  activity_member_remove: '义工成员移除',
  activity_member_review: '义工成员审核',
  import_excel: '数据导入',
  export: '数据导出',
  attachment: '附件',
  attachment_view: '附件查看',
  attachment_audit: '附件审计',
  attachment_upload: '附件上传',
  attachment_status: '附件裁决',
  attachment_link: '附件外链',
  attachment_verify: '附件查证',
  attachment_bind: '附件绑定',
  attachment_delete: '附件删除',
  attachment_ocr_enqueue: '附件 OCR 排队',
  changelog_create: '更新日志创建',
  changelog_update: '更新日志修改',
  changelog_delete: '更新日志删除',
  notification_create: '通知创建',
  notification_update: '通知修改',
  notification_delete: '通知删除',
  notification_rollback: '通知回滚',
  ban_change: '封禁变更',
  ban_rule: '封禁规则',
  ban_rule_skipped: '封禁规则跳过',
  binding_mismatch: '设备绑定不符',
  device_bind: '设备绑定',
  device_unbind: '设备解绑',
  device_ban: '设备封禁',
  device_ban_skipped: '设备封禁跳过',
  device_mismatch: '设备不匹配',
  device_required: '缺少学海设备',
  security_alert: '安全告警',
  risk_alert: '风险告警',
  risk_ban: '风险封禁',
  auth_maintenance_blocked: '维护期间登录拦截',
  system_maintenance_enable: '开启维护模式',
  system_maintenance_disable: '关闭维护模式',
  system_dual_auth: '双重验证',
  system_op: '系统操作',
  system_ssh: 'Web SSH',
  system_ssh_command: 'SSH 指令',
  system_ssh_suggest: 'SSH 指令建议',
  system_file: '文件操作',
  system_db_read: '数据库查询',
  system_db_query: '数据库查询',
  system_db_dml: '数据库操作',
  system_db_ddl: '数据库结构变更',
  system_settings_update: '系统设置变更',
  system_user_quota_update: '用户额度修改',
  retention_purge: '留存清理',
  user_trash_gc: '用户回收站清理',
  adb_backup: 'ADB 备份',
  temp_password_create: '临时密码创建',
  temp_password_login: '临时密码登录',
  temp_password_fail: '临时密码错误',
  ai_api_key_create: 'AI 凭据新增',
  ai_api_key_update: 'AI 凭据修改',
  ai_api_key_delete: 'AI 凭据删除',
  ai_key_create: 'AI 凭据新增',
  ai_key_update: 'AI 凭据修改',
  ai_key_delete: 'AI 凭据删除',
  ai_ocr_engine_create: 'OCR 引擎新增',
  ai_ocr_engine_update: 'OCR 引擎修改',
  ai_ocr_engine_delete: 'OCR 引擎删除',
  ai_ocr_enqueue: 'OCR 排队',
  ai_prompt_access: '提示词查看',
  ai_quota_set: 'AI 额度设置',
  ai_quota_reset: 'AI 额度重置',
  ai_quota_request_approve: '配额申请通过',
  ai_quota_request_reject: '配额申请拒绝',
  ai_moderation_unrestricted: '关闭内容审核',
  ai_safety_guard_error: '安全守卫异常',
  ai_safety_guard_scheduled_skip: '安全守卫跳过',
  agent_jailbreak_warning: '越狱提示拦截',
  ai_governance: 'AI 治理',
  ai_ops_extra: 'AI 运维操作',
  platform_misuse_report: '平台滥用举报',
  platform_misuse_violation: '平台滥用认定',
  platform_misuse_exempt: '平台滥用豁免'
}

const levelLabels: Record<string, string> = {
  info: '普通',
  warning: '警告',
  error: '错误',
  debug: '调试',
  verbose: '详细'
}

// 系统审计日志:data 是 JSON,展开成人名而不是 oid 哈希;action 语义给出中文标签
const AUDIT_TYPES = new Set([
  'system_dual_auth',
  'system_op',
  'system_ssh',
  'system_ssh_command'
])

const auditActionLabels: Record<string, string> = {
  web_ssh: 'Web SSH',
  ssh: 'Web SSH',
  files: '文件管理',
  db_content: '资料中心',
  maintenance: '维护',
  system: '系统'
}

const UNKNOWN_NAME = '未知用户'

function auditName(value: unknown): string {
  if (typeof value === 'string' && value.length > 0) {
    return value
  }
  return UNKNOWN_NAME
}

const auditDetail = computed(() => {
  const type = log.value?.type
  if (!type || !AUDIT_TYPES.has(type)) {
    return null
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(log.value.data)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return null
  }
  const doc = parsed as Record<string, unknown>
  const action = doc.action
  const actionLabel =
    typeof action === 'string' && action.length > 0 ? (auditActionLabels[action] ?? action) : ''
  const operator = auditName(doc.operator_name)
  const detail = typeof doc.detail === 'string' ? doc.detail : ''

  if (type === 'system_ssh_command') {
    const command = typeof doc.command === 'string' ? doc.command : ''
    return { text: `${operator} $ ${command}`, mono: true }
  }
  if (type === 'system_dual_auth') {
    const approver = auditName(doc.approver_name)
    const text = action
      ? `${actionLabel} · 操作人 ${operator} · 审批人 ${approver}`
      : `操作人 ${operator} · 审批人 ${approver}`
    const lines = [text]
    if (typeof doc.reason === 'string' && doc.reason) {
      lines.push(`原因：${doc.reason}`)
    }
    return { text: lines.join('\n'), mono: false }
  }
  const parts: string[] = []
  if (typeof action === 'string' && action) parts.push(actionLabel || action)
  parts.push(operator)
  if (detail) parts.push(detail)
  return { text: parts.join(' · '), mono: false }
})
</script>

<template>
  <div class="py-1 yg-form-wrap">
    <ElCard shadow="hover">
      <ElForm>
        <ElFormItem label="类型">
          <ElTag v-if="log.type" size="small">{{ typeLabels[log.type] ?? log.type }}</ElTag>
          <ElTag
            v-if="log.level && log.level !== 'info'"
            size="small"
            type="warning"
          >
            {{ levelLabels[log.level] ?? log.level }}
          </ElTag>
        </ElFormItem>
        <ElFormItem label="用户">
          <ElTag v-if="!log.user && !log.user_name" size="small" type="info">匿名</ElTag>
          <div v-else class="leading-snug">
            <div>{{ log.user_name || '未知用户' }}</div>
            <div
              v-if="log.user_id"
              class="text-xs"
              style="color: var(--yg-text-tertiary)"
            >
              {{ log.user_id }}
            </div>
          </div>
        </ElFormItem>
        <ElFormItem v-if="log.xuehai_id" label="学海 ID">
          {{ log.xuehai_id }}
        </ElFormItem>
        <ElFormItem v-if="clientIp" label="IP">
          {{ clientIp }}
        </ElFormItem>
        <ElFormItem v-if="userAgent" label="UA" style="overflow-wrap: break-word">
          <span :title="userAgent">{{ userAgent }}</span>
        </ElFormItem>
        <ElFormItem label="日期">
          {{ dayjs.unix(log.timestamp).format('YYYY-MM-DD HH:mm:ss') }}
        </ElFormItem>
        <ElFormItem label="详情" style="overflow-wrap: break-word; white-space: pre-wrap">
          <!-- 审计日志解析成功:节到 3 行并给出 title 悬浮全文,避免长详情占满卡片;
               解析失败或其它类型:维持原文展示,不吞内容 -->
          <span
            v-if="auditDetail"
            :class="auditDetail.mono ? 'font-mono' : ''"
            class="audit-text"
            :title="auditDetail.text"
          >{{ auditDetail.text }}</span>
          <template v-else>{{ log.data }}</template>
        </ElFormItem>
        <ElFormItem label="URL">
          {{ log.url }}
        </ElFormItem>
      </ElForm>
    </ElCard>
  </div>
</template>

<style scoped>
/* 解析后的审计详情:最多 3 行截断,title 悬浮已有全文 */
.audit-text {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}
</style>