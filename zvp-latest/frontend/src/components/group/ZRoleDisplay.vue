<script setup>
import { toRefs, ref, onMounted, watch } from 'vue'
import { QuestionFilled, ArrowRight } from '@element-plus/icons-vue'
import { positions as roles } from '@/icons/positions'
import { ElIcon, ElButton } from 'element-plus'

const roleNames = {
    secretary: '团支书',
    department: '学生会',
    admin: '管理员',
    teacher: '教师',
}

const props = defineProps({
    role: { type: String, required: true },
})

const { role } = toRefs(props)

</script>

<template>
    <ElIcon
        class="flex-shrink-0"
        :style="`color: var(--el-color-${roles[role]?.color ?? 'info'}); font-size: large;`"
    >
        <Component :is="roles[role]?.icon ?? QuestionFilled"></Component>
    </ElIcon>
    <div class="group-info flex flex-col flex-grow">
        <pre class="group-name" style="padding: 0; margin: 0; font-family: inherit">{{ roleNames[role] ?? role }}</pre>
    </div>
    <ElButton
        text bg type="primary"
        @click="$router.push(
            `/manage/roles/${role}`
        )"
    >
        查看
        <ElIcon class="el-icon--right"><ArrowRight></ArrowRight></ElIcon>
    </ElButton>
</template>

<style scoped>
.group-info, .group-name {
    overflow: hidden;
}

.group-name {
    text-overflow: ellipsis;
}
</style>