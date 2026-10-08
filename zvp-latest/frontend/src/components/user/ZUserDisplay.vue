<script setup>
import { toRefs, ref, onMounted, watch } from 'vue'
import { User, ArrowRight } from '@element-plus/icons-vue'
import { ElIcon, ElButton, ElTag } from 'element-plus'

const roleNames = {
    secretary: '团支书',
    department: '学生会',
    admin: '管理员',
    teacher: '教师',
}

const props = defineProps({
    user: { type: Object, required: true },
})

const { user } = toRefs(props)

</script>

<template>
    <ElIcon
        class="flex-shrink-0"
        style="color: var(--el-color-primary); font-size: large;"
    >
        <User></User>
    </ElIcon>
    <div class="user-info flex flex-col flex-grow">
        <div class="user-profile flex flex-row">
            <pre class="user-name" style="padding: 0; margin: 0; font-family: inherit">{{ user.name }}</pre>
            <pre v-if="user.class_name" class="user-class" style="padding: 0; margin: 0; font-family: inherit">{{ user.class_name }}</pre>
            <div v-for="role in user.roles" class="tag-container flex flex-row flex-shrink-0">
                <ElTag size="small" effect="plain" disable-transitions class="flex-shrink-0">
                    {{ roleNames[role] ?? role }}
                </ElTag>
            </div>
        </div>
    </div>
        <div class="user-attrs flex flex-row">
            <div>
                {{ user.id }}
            </div>
        </div>
    <ElButton
        text bg type="primary"
        @click="$router.push(
            `/user/${user._id}`
        )"
    >
        查看
        <ElIcon class="el-icon--right"><ArrowRight></ArrowRight></ElIcon>
    </ElButton>
</template>

<style scoped>
.user-info, .user-name, .user-profile {
    overflow: hidden;
}

.user-name {
    text-overflow: ellipsis;
}

.user-attrs {
    color: var(--el-text-color-secondary);
    gap: 1em;
    align-items: center;
    font-size: small;
    flex-shrink:0;
}

.user-attrs > * {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: .5em;
}

.user-profile {
    gap: .5em;
    align-items: center;
}

.user-class {
    color: var(--el-text-color-secondary);
}

.tag-container {
    height: 0;
    max-height: 0;
    overflow: visible;
    align-items: center;
}
</style>