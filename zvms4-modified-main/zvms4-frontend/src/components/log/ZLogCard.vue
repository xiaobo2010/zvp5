<script setup lang="ts">
import type { Log } from '../../../types/log'
import { computed, toRefs } from 'vue'
import { ElCard, ElForm, ElFormItem, ElTag } from 'element-plus'
import dayjs from '@/plugins/dayjs'

const props = defineProps<{
  log: Log
}>()

const { log } = toRefs(props)

const typeLabels: Record<string, string> = {
  user_login: '登录',
  user_verify: '身份验证',
  passwd_change: '密码修改',
  totp_view: '动态码查看',
  user_create: '用户创建',
  role_change: '角色变更',
  past_change: '历史身份变更',
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
  import_excel: '数据导入',
  export: '数据导出'
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
  'system_files',
  'system_db_content',
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
  <div class="py-1 z-wrap">
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
              style="color: var(--zv-label)"
            >
              {{ log.user_id }}
            </div>
          </div>
        </ElFormItem>
        <ElFormItem v-if="log.xuehai_id" label="学海 ID">
          {{ log.xuehai_id }}
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