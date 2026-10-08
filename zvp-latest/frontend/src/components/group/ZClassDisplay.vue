<script setup>
import { toRefs, ref, onMounted, watch } from 'vue'
import { ArrowRight, Edit, Delete } from '@element-plus/icons-vue'
import PhStudent from '@/icons/positions/PhStudent.vue'
import MdiEye from '@/icons/MdiEye.vue'
import { ElIcon, ElButton } from 'element-plus'

const props = defineProps({
    group: { type: Object, required: true },
})

const { group } = toRefs(props)

const devNotice = () => alert('功能开发中')

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
        class="action-btn flex-shrink-0"
        :icon="Edit"
        text bg type="warning"
        @click="devNotice()"
    >
        修改
    </ElButton>
    <ElButton
        class="action-btn flex-shrink-0"
        :icon="Delete"
        text bg type="danger"
        @click="devNotice()"
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