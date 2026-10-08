<script setup>
import { toRefs, ref, onMounted, watch } from 'vue'
import { User, Calendar, QuestionFilled, ArrowRight, Timer } from '@element-plus/icons-vue'
import { ElIcon, ElButton } from 'element-plus'
import dayjs from '@/plugins/dayjs'
import classifications from '@/components/tags/classifications.ts'

const { mode: modes, member: status } = classifications

const modeNames = {
    'on-campus': '校内义工',
    'off-campus': '校外义工',
    'social-practice': '社会实践'
}

const statusNames = {
    pending: '审核中',
    effective: '有效',
    refused: '无效',
    draft: '草稿', // reserved
}

const props = defineProps({
    activity: { type: Object, required: true },
    showButton: { type: Boolean, default: true },
})

const { activity, showButton } = toRefs(props)

</script>

<template>
    <ElIcon
        class="flex-shrink-0"
        :style="`color: var(--el-color-${modes[
            activity.mine?.mode ?? activity.type
        ]?.color ?? 'info'}); font-size: large;`"
    >
        <Component :is="modes[activity.mine?.mode ?? activity.type]?.icon ?? QuestionFilled"></Component>
    </ElIcon>
    <div class="activity-info flex flex-col flex-grow">
        <pre class="activity-name" style="padding: 0; margin: 0; font-family: inherit">{{ activity.name }}</pre>
        <div class="activity-attrs flex flex-row">
            <div>
                {{ modeNames[activity.mine?.mode ?? activity.type] ?? '特殊义工' }}
            </div>
            <div>
                <ElIcon><Calendar></Calendar></ElIcon>
                <span>{{ dayjs(activity.date).format('YYYY-MM-DD') }}</span>
            </div>
            <div v-if="activity.mine">
                <ElIcon><Timer></Timer></ElIcon>
                <span>{{ activity.mine.duration }}h</span>
            </div>
            <div>
                <ElIcon>
                    <Component :is="status[activity.status]?.icon ?? QuestionFilled"></Component>
                </ElIcon>
                <span>{{ statusNames[activity.status] ?? '未知状态' }}</span>
            </div>
            <div>
                <ElIcon><User></User></ElIcon>
                <span>{{ activity.creator_name }}</span>
            </div>
        </div>
    </div>
    <ElButton
        v-if="showButton" text bg type="primary"
        @click="$router.push(
            `/activity/details/${activity._id}` + (activity.mine ? `?session=${activity.mine._id}` : '')
        )"
    >
        查看详情
        <ElIcon class="el-icon--right"><ArrowRight></ArrowRight></ElIcon>
    </ElButton>
</template>

<style scoped>
.activity-info, .activity-name {
    overflow: hidden;
}

.activity-name {
    text-overflow: ellipsis;
}

.activity-attrs {
    color: var(--el-text-color-secondary);
    gap: 1em;
    align-items: center;
    font-size: small;
}

.activity-attrs > * {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: .5em;
}
</style>