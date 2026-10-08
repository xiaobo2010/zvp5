<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { ref, watch, computed, reactive } from 'vue'
import ZList from '@/components/utils/ZList.vue'
import ZUserDisplay from '@/components/user/ZUserDisplay.vue'
import {
    ElPageHeader
} from 'element-plus'

const state = useState()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const role = ref(route.params?.role)

const isAdmin = computed(() => userStore.position.includes('admin'))

const getUsers = async (options) => await (await import('@/api')).user.read_v2({...options, role:role.value})

const usersTab = reactive({
    page:     Number(route.query?.page     ?? 1),
    pageSize: Number(route.query?.pagesize ?? 10),
    query:    route.query?.query           ?? ''
})

const roleNames = {
    secretary: '团支书',
    department: '学生会',
    admin: '管理员',
    teacher: '教师',
}


const updateRoute = () => {
    router.replace({
        query: {
            page:           usersTab.page,
            pagesize:       usersTab.pageSize,
            query:          usersTab.query
        }
    })
}

watch(() => usersTab.page,       updateRoute)
watch(() => usersTab.pageSize,   updateRoute)
watch(() => usersTab.query,      updateRoute)

watch(() => route.params?.role,    v => { role.value          = v })
watch(() => route.query?.page,     v => { usersTab.page       = Number(v ?? 1)})
watch(() => route.query?.pagesize, v => { usersTab.pageSize   = Number(v ?? 10)})
watch(() => route.query?.query,    v => { usersTab.query      = v        ?? ''})

</script>

<template>
    <div class="manage-panel flex flex-col">
        <ElPageHeader class="manage-panel-header flex-shrink-0" @back="$router.back()">
            <template #content>
                {{roleNames[role] ?? `未知权限：${role}`}}
            </template>
        </ElPageHeader>
                <ZList
                    v-if="isAdmin"
                    :remote-method="getUsers"
                    v-model:page="usersTab.page"
                    v-model:page-size="usersTab.pageSize"
                    v-model:query="usersTab.query"
                    search-button
                    v-slot="{ item }"
                    :key="role"
                >
                    <ZUserDisplay :user="item"></ZUserDisplay>
                </ZList>
        <span v-else style="color: var(--el-color-danger)">
            您没有权限查看此页面
        </span>
    </div>
</template>

<style scoped>
.manage-panel {
    padding:2rem;
    gap: 2rem;
    background: var(--el-bg-color);
}

.manage-tabs, .manage-tabs .pane {
    flex-grow: 1;
    flex-shrink: 1;
    overflow: hidden;
}

.dashboard-pane {
    gap: .5rem;
}

.section-title {
    font-size: large;
    font-weight: bold;
}
</style>
