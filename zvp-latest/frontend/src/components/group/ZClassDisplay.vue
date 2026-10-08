<script setup>
import { toRefs, ref, onMounted, watch, computed } from 'vue'
import { ArrowRight, Edit, Delete } from '@element-plus/icons-vue'
import PhStudent from '@/icons/positions/PhStudent.vue'
import MdiEye from '@/icons/MdiEye.vue'
import { ElIcon, ElButton, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores'

const props = defineProps({
    group: { type: Object, required: true },
})

const emits = defineEmits(['updated'])

const { group } = toRefs(props)

const user = useUserStore()

const isAdmin = computed(() => user.position.includes('admin'))

const loading = ref(false)

const renameGroup = async () => {
    loading.value = true
    try {
        const { value } = await ElMessageBox.prompt(
            '请输入新的班级名',
            `修改${group.value.name}的名称`,
            {
                confirmButtonText: '确定',
                cancelButtonText: '取消',
                showClose: false,
                closeOnClickModal: false,
            }
        )
        if (value) {
            await (await import('@/api')).group.rename_v3(group.value._id, value)
            emits('updated')
        }
    } finally {
        loading.value = false
    }
}

const deleteGroupAction = async () => {
    loading.value = true
    try {
        await ElMessageBox.confirm(
            `确定删除班级「${group.value.name}」吗？班级成员将被移出。`,
            '删除班级',
            {
                confirmButtonText: '删除',
                confirmButtonClass: 'el-button--danger',
                cancelButtonText: '取消',
                type: 'warning',
                closeOnClickModal: false,
            }
        )
        await (await import('@/api')).group.delete_v3(group.value._id)
        emits('updated')
    } catch(_) {
    } finally {
        loading.value = false
    }
}

</script>

<template>
    <ElIcon
        class="flex-shrink-0"
        style="color: var(--el-color-primary); font-size: large;"
    >
        <PhStudent></PhStudent>
    </ElIcon>
    <div class="group-info flex flex-col flex-grow">
        <pre class="group-name" style="padding: 0; margin: 0; font-family: inherit">{{ group.name }}</pre>
    </div>
    <ElButton
        class="action-btn flex-shrink-0"
        :icon="MdiEye"
        text bg type="primary"
        @click="$router.push(
            `/group/${group._id}`
        )"
    >
        查看
    </ElButton>
    <ElButton
        v-if="isAdmin"
        class="action-btn flex-shrink-0"
        :icon="Edit"
        :disabled="loading"
        text bg type="warning"
        @click="renameGroup()"
    >
        修改
    </ElButton>
    <ElButton
        v-if="isAdmin"
        class="action-btn flex-shrink-0"
        :icon="Delete"
        :disabled="loading"
        text bg type="danger"
        @click="deleteGroupAction()"
    >
        删除
    </ElButton>
</template>

<style scoped>
.group-info, .group-name {
    overflow: hidden;
}

.group-name {
    text-overflow: ellipsis;
}

.action-btn {
    margin: 0;
}
</style>