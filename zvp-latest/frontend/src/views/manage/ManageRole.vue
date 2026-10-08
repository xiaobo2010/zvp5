<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { ref, watch, computed, reactive } from 'vue'
import ZList from '@/components/utils/ZList.vue'
import ZUserDisplay from '@/components/user/ZUserDisplay.vue'
import { Edit } from '@element-plus/icons-vue'
import {
    ElPageHeader,
    ElButton,
    ElDialog,
    ElForm,
    ElFormItem,
    ElCheckboxGroup,
    ElCheckbox
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

const editRole = ref(false)
const editing = ref(false)
const target = ref(null)
const checkedRoles = ref([])
const usersVersion = ref(0)

const openRoleEdit = (u) => {
    target.value = u
    checkedRoles.value = (u.roles ?? []).filter(r => roleNames[r])
    editRole.value = true
}

const doEditRole = async () => {
    try {
        editing.value = true
        await (await import('@/api')).user.updateRoles(target.value._id, checkedRoles.value)
        editRole.value = false
        usersVersion.value++
    } finally {
        editing.value = false
    }
}

</script>

<template>
    <div class="manage-panel flex flex-col">
        <ElPageHeader class="manage-panel-header flex-shrink-0" @back="$router.back()">
            <template #content>
                {{roleNames[role] ?? `未知权限：${role}`}}
            </template>
        </ElPageHeader>
        <ElDialog
            v-model="editRole"
            :close-on-click-modal="false"
            align-center
            :show-close="!editing"
            :close-on-press-escape="!editing"
        >
            <template #header>
                修改角色{{target ? `：${target.name}` : ''}}
            </template>
            <ElForm v-loading="editing">
                <ElFormItem label="角色">
                    <ElCheckboxGroup v-model="checkedRoles">
                        <ElCheckbox
                            v-for="(name, value) in roleNames"
                            :key="value"
                            :value="value"
                        >{{name}}</ElCheckbox>
                    </ElCheckboxGroup>
                </ElFormItem>
                <ElFormItem>
                    <span style="color: var(--el-text-color-secondary); font-size: small;">不勾选任何角色则为普通学生</span>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doEditRole()"
                    :disabled="editing"
                >
                    修改
                </ElButton>
            </template>
        </ElDialog>
                <ZList
                    v-if="isAdmin"
                    :remote-method="getUsers"
                    v-model:page="usersTab.page"
                    v-model:page-size="usersTab.pageSize"
                    v-model:query="usersTab.query"
                    search-button
                    v-slot="{ item }"
                    :key="role + '-' + usersVersion"
                >
                    <ZUserDisplay :user="item"></ZUserDisplay>
                    <ElButton
                        v-if="isAdmin"
                        class="flex-shrink-0"
                        :icon="Edit"
                        text bg type="warning"
                        @click="openRoleEdit(item)"
                    >
                        修改角色
                    </ElButton>
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
