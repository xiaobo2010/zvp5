<script setup>
import { toRefs } from 'vue'
import { ElCard, ElDescriptions, ElDescriptionsItem as Item } from 'element-plus'
import dayjs from '@/plugins/dayjs'
import { logTypes, logLevels } from '@/utils/constants'

const props = defineProps({
    log: { type: Object, required: true }
})

const { log } = toRefs(props)

const emits = defineEmits(['track'])

const track = prop => emits('track', {
    key: prop,
    value: log.value[prop]
})
</script>

<template>
    <ElCard :class="['log-card', `log-card--${log.level}`]" shadow="never">
        <ElDescriptions :column="1">
            <Item label="级别">
                {{ logLevels[log.level] ?? log.level }}
            </Item>
            <Item label="类型">
                {{ logTypes[log.type] ?? log.type }}
            </Item>
            <Item label="用户">
                <span @dblclick="track('user')">{{ log.user_name || log.user }}</span>
            </Item>
            <Item label="时间">
                {{ dayjs(log.timestamp).format('YYYY-MM-DD HH:mm:ss') }}
            </Item>
            <Item label="详情">
                {{ log.summary }}
            </Item>
            <Item label="会话">
                <span @dblclick="track('session')">{{ log.session }}</span>
            </Item>
            <Item label="设备">
                <span @dblclick="track('net_session')">{{ log.net_session }}</span>
            </Item>
            <Item label="IP">
                {{ log.request.ip }}
            </Item>
            <Item label="URL">
                {{ log.request.method }} {{ log.request.url }}
            </Item>
        </ElDescriptions>
    </ElCard>
</template>

<style scoped>
.log-card {
    flex-grow: 1;
    border-radius: .5rem;
}
.log-card, .log-card * {
    white-space: pre-wrap;
    overflow-wrap: break-word;
    word-break: break-all;
    font-family: 'Ubuntu Mono', monospace, Ubuntu, 'PingFang SC', 'Source Han Sans SC';
}

/*.log-card {
    --el-text-color-primary: red;
    --el-text-color-regular: green;
    --el-fill-color-blank: magenta;
    --el-border-color-light: cyan;
}*/

.log-card--warning {
    --el-text-color-primary: var(--el-color-warning-dark-2);
    --el-text-color-regular: var(--el-color-warning);
    --el-fill-color-blank: var(--el-color-warning-light-9);
    --el-border-color-light: var(--el-color-warning-light-8);
}

.log-card--debug, .log-card--verbose {
    --el-text-color-primary: var(--el-color-info-dark-2);
    --el-text-color-regular: var(--el-color-info);
    --el-fill-color-blank: var(--el-color-info-light-9);
    --el-border-color-light: var(--el-color-info-light-8);
}
</style>