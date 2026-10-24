<script setup lang="ts">
import type { Log } from '@/types/log'
import { toRefs } from 'vue'
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import dayjs from '@/plugins/dayjs'
import { ElCard, ElForm, ElFormItem } from 'element-plus'

const props = defineProps<{
  log: Log
}>()

const { log } = toRefs(props)

</script>

<template>
  <div class="z-wrap">
    <ElCard class="log-card" shadow="never">
      <ElForm>
        <ElFormItem label="用户">
          <ZActivityMember :id="log.user" />
        </ElFormItem>
        <ElFormItem label="用户 ID">
          {{ log.user }}
        </ElFormItem>
        <ElFormItem label="日期">
          {{ dayjs.unix(log.timestamp).format('YYYY-MM-DD HH:mm:ss') }}
        </ElFormItem>
        <ElFormItem label="详情" style="overflow-wrap: break-word; white-space: pre-wrap">
          {{ log.data }}
        </ElFormItem>
        <ElFormItem label="IP" v-if="log.ip !== '127.0.0.1'">
          {{ log.ip }}
        </ElFormItem>
        <ElFormItem label="Clarity ID" v-if="log.clarity">
          {{ log.clarity }}
        </ElFormItem>
        <ElFormItem label="学海 ID" v-if="log.xuehai || log.xuehai_id">
          {{ log.xuehai || log.xuehai_id }}
        </ElFormItem>
        <ElFormItem label="URL">
          {{ log.url }}
        </ElFormItem>
      </ElForm>
    </ElCard>
  </div>
</template>

<style scoped>
.log-card * {
    overflow-wrap: break-word;
    word-break: break-all;
}
</style>
