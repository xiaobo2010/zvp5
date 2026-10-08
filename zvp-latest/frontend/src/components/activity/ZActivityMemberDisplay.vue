<script setup>
import { toRefs, ref, onMounted, watch, computed } from 'vue'
import { User, Calendar, QuestionFilled, ArrowRight, Timer, Edit, Delete } from '@element-plus/icons-vue'
import { ElButton, ElIcon, ElMessageBox } from 'element-plus'

import classifications from '@/components/tags/classifications.ts'
import { useUserStore } from '@/stores'

const { mode: modes, member: status } = classifications

const user = useUserStore()

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
    activityId: { type: String, required: true },
    member: { type: Object, required: true },
    statusOverride: { type: String, default: '' },
})

const emits = defineEmits(['updated'])

const { activityId, member, statusOverride } = toRefs(props)

const hasPermission = computed(() => user.position.includes('admin') || user.position.includes('department'))

const devNotice = () => alert('功能开发中')

const loading = ref(false)

const modifyMember = async () => {
    loading.value = true
    try {
        const dur = Number(
            (
                await ElMessageBox.prompt(
                    '请输入义工时间（单位：小时）',
                    `修改${member.value.member_name}的义工时间`,
                    {
                        confirmButtonText: '确定',
                        cancelButtonText: '取消',
                        inputType: 'number',
                        showClose: false,
                        closeOnClickModal: false,
                    }
                )
            ).value
        )
        if (dur === dur) { // not NaN
            await (await import('@/api')).activity.duration.modify(
                member.value._id,
                activityId.value,
                dur,
                member.value.mode,
                false
            )
            emits('updated')
        }
    } finally {
        loading.value = false
    }
}

const deleteMember = async () => {
    loading.value = true
    try {
        await (await import('@/api')).activity.member.remove(member.value._id, activityId.value, user._id)
        emits('updated')
    } finally {
        loading.value = false
    }
}

</script>

<template>
    <ElIcon
        class="flex-shrink-0"
        :style="`color: var(--el-color-${modes[
            member.mode
        ]?.color ?? 'info'}); font-size: large;`"
    >
        <Component :is="modes[member.mode]?.icon ?? QuestionFilled"></Component>
    </ElIcon>
    <div class="activity-info flex flex-col flex-grow">
        <pre class="activity-name" style="padding: 0; margin: 0; font-family: inherit">{{ member.member_name }}</pre>
        <div class="activity-attrs flex flex-row">
            <div>
                {{ modeNames[member.mode] ?? '特殊义工' }}
            </div>
            <div>
                <ElIcon><Timer></Timer></ElIcon>
                <span>{{ member.duration }}h</span>
            </div>
            <div>
                <ElIcon>
                    <Component :is="status[statusOverride || member.status]?.icon ?? QuestionFilled"></Component>
                </ElIcon>
                <span>{{ statusNames[statusOverride || member.status] ?? '未知状态' }}</span>
            </div>
        </div>
    </div>
    <ElButton
        v-if="hasPermission"
        class="action-btn flex-shrink-0"
        :icon="Edit"
        :disabled="loading"
        text bg type="warning"
        @click="modifyMember()"
    >
        修改
    </ElButton>
    <ElButton
        v-if="hasPermission"
        class="action-btn flex-shrink-0"
        :icon="Delete"
        :disabled="loading"
        text bg type="danger"
        @click="deleteMember()"
    >
        删除
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

.action-btn {
    margin: 0;
}
</style>