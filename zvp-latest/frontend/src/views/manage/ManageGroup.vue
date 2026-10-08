<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { ref, watch, computed, reactive, onMounted } from 'vue'
import ZList from '@/components/utils/ZList.vue'
import ZUserDisplay from '@/components/user/ZUserDisplay.vue'
import { Plus, Delete } from '@element-plus/icons-vue'
import {
    ElPageHeader,
    ElButton,
    ElDialog,
    ElForm,
    ElFormItem,
    ElSelect,
    ElOption,
    ElMessageBox
} from 'element-plus'

const state = useState()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const group = ref(route.params?.group)

const groupObj = ref(null)

const load = async () => {
    try {
        // TODO use V2 api
        groupObj.value = await (await import('@/api')).group.readOne_v2(group.value)
    } catch(_) {
        router.back()
    }
}

const hasPerm = computed(() => userStore.position.includes('admin') || userStore.position.includes('department'))

const getUsers = async (options) => await (await import('@/api')).group.reads.users_v3(group.value, options)

const usersVersion = ref(0)

const add = ref(false)
const adding = ref(false)
const selectedUser = ref('')
const userOptions = ref([])
const userLoading = ref(false)

const resetAddForm = () => {
    selectedUser.value = ''
    userOptions.value = []
}

const searchUsers = async (query) => {
    userLoading.value = true
    try {
        const { data } = await (await import('@/api')).user.read_v2({ page: 1, pageSize: 20, query })
        userOptions.value = data
    } finally {
        userLoading.value = false
    }
}

const doAdd = async () => {
    try {
        adding.value = true
        await (await import('@/api')).group.addUser_v3(group.value, selectedUser.value)
        add.value = false
        usersVersion.value++
    } finally {
        adding.value = false
    }
}

const removing = ref(false)

const exportTemplate = async () => {
    try {
        exporting.value = true
        const api = await import('@/api')
        const blob = await api.exports.template(group.value)
        api.exports.save(blob, `${groupObj.value?.name ?? group.value}-template.xlsx`)
    } finally {
        exporting.value = false
    }
}

const exporting = ref(false)

const removeUser = async (u) => {
    removing.value = true
    try {
        await ElMessageBox.confirm(
            `确定将「${u.name}」移出班级吗？`,
            '移出班级',
            {
                confirmButtonText: '移出',
                cancelButtonText: '取消',
                type: 'warning',
                closeOnClickModal: false,
            }
        )
        await (await import('@/api')).group.removeUser_v3(group.value, u._id)
        usersVersion.value++
    } catch(_) {
    } finally {
        removing.value = false
    }
}

const usersTab = reactive({
    page:     Number(route.query?.page     ?? 1),
    pageSize: Number(route.query?.pagesize ?? 10),
    query:    route.query?.query           ?? ''
})


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

watch(() => route.params?.group,    v => { group.value          = v })
watch(() => route.query?.page,     v => { usersTab.page       = Number(v ?? 1)})
watch(() => route.query?.pagesize, v => { usersTab.pageSize   = Number(v ?? 10)})
watch(() => route.query?.query,    v => { usersTab.query      = v        ?? ''})

watch(group, load)
onMounted(load)

</script>

<template>
    <div class="manage-panel flex flex-col">
        <ElPageHeader class="manage-panel-header flex-shrink-0" @back="$router.back()">
            <template #content>
                {{groupObj?.name}}
            </template>
            <template #extra>
                <ElButton
                    v-if="hasPerm"
                    :icon="Plus"
                    text bg type="success"
                    @click="add = true"
                >
                    添加成员
                </ElButton>
                <ElButton
                    v-if="hasPerm"
                    text bg type="primary"
                    :disabled="exporting"
                    @click="exportTemplate()"
                >
                    导出模板
                </ElButton>
            </template>
        </ElPageHeader>
        <ElDialog
            v-model="add"
            @open="resetAddForm()"
            :close-on-click-modal="false"
            align-center
            :show-close="!adding"
            :close-on-press-escape="!adding"
        >
            <template #header>
                添加成员
            </template>
            <ElForm v-loading="adding">
                <ElFormItem label="用户">
                    <ElSelect
                        v-model="selectedUser"
                        filterable remote
                        :remote-method="searchUsers"
                        :loading="userLoading"
                        placeholder="搜索用户"
                    >
                        <ElOption
                            v-for="u in userOptions"
                            :key="u._id"
                            :label="`${u.name} (${u.id})`"
                            :value="u._id"
                        ></ElOption>
                    </ElSelect>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doAdd()"
                    :disabled="adding || !selectedUser"
                >
                    添加
                </ElButton>
            </template>
        </ElDialog>
                <ZList
                    v-if="hasPerm"
                    :remote-method="getUsers"
                    v-model:page="usersTab.page"
                    v-model:page-size="usersTab.pageSize"
                    v-model:query="usersTab.query"
                    search-button
                    v-slot="{ item }"
                    :key="group + '-' + usersVersion"
                >
                    <ZUserDisplay :user="item"></ZUserDisplay>
                    <ElButton
                        v-if="hasPerm"
                        class="flex-shrink-0"
                        :icon="Delete"
                        :disabled="removing"
                        text bg type="danger"
                        @click="removeUser(item)"
                    >
                        移出班级
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
