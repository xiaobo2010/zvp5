<script setup lang="ts">
import type { Log } from '../../../types/log'
import { toRefs } from 'vue'
import { ElCard, ElForm, ElFormItem, ElTag } from 'element-plus'
import { ZActivityMember } from '@/components'
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
          <ZActivityMember v-if="log.user" :id="log.user" />
          <ElTag v-else size="small" type="info">未登录</ElTag>
        </ElFormItem>
        <ElFormItem label="用户 ID">
          {{ log.user }}
        </ElFormItem>
        <ElFormItem v-if="log.xuehai_id" label="学海 ID">
          {{ log.xuehai_id }}
        </ElFormItem>
        <ElFormItem label="日期">
          {{ dayjs.unix(log.timestamp).format('YYYY-MM-DD HH:mm:ss') }}
        </ElFormItem>
        <ElFormItem label="详情" style="overflow-wrap: break-word; white-space: pre-wrap">
          {{ log.data }}
        </ElFormItem>
        <ElFormItem label="URL">
          {{ log.url }}
        </ElFormItem>
      </ElForm>
    </ElCard>
  </div>
</template>

<style scoped></style>