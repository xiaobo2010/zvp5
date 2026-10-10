<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { ref, watch, computed, reactive } from 'vue'
import ZLogCard from '@/components/log/ZLogCard.vue'
import ZList from '@/components/utils/ZList.vue'
import ZUserDisplay from '@/components/user/ZUserDisplay.vue'
import ZClassDisplay from '@/components/group/ZClassDisplay.vue'
import ZRoleDisplay from '@/components/group/ZRoleDisplay.vue'
import ZLogDisplay from '@/components/log/ZLogDisplay.vue'
import ZDisplayTOTP from '@/components/utils/ZDisplayTOTP.vue'
import {
    ElPageHeader,
    ElButton,
    ElTabs,
    ElTabPane,
    ElDivider,
    ElSelect,
    ElOption,
    ElIcon,
    ElInput,
    ElInputNumber,
    ElDialog,
    ElForm,
    ElFormItem,
    ElRow,
    ElCol,
    ElCard,
    ElStatistic
} from 'element-plus'
import { CircleClose, Key, Plus } from '@element-plus/icons-vue'
import { logTypes, logLevels } from '@/utils/constants'

const state = useState()

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.position.includes('admin'))
const isRestricted = computed(
    () =>
        !isAdmin.value && !userStore.position.includes('department') && userStore.position.includes('secretary')
        && userStore.class_id
)
const canView = computed(
    () => userStore.position.includes('department') || isAdmin.value || isRestricted.value
)

const defaultTab = computed(() => isAdmin.value ? 'dashboard' : isRestricted.value ? 'users' : 'classes')

const pane = ref(route.query?.tab ?? defaultTab.value)

const getClasses = async ({
    page, pageSize, query
}) => await (await import('@/api')).group.read_v2(page, pageSize, query)

const classesTab = reactive({
    page:     Number(route.query?.classes_page     ?? 1),
    pageSize: Number(route.query?.classes_pagesize ?? 10),
    query:    route.query?.classes_query           ?? ''
})

const getUsers = async (options) => await (await import('@/api')).user.read_v2(options)
const restrictedGetUsers = async (options) => await (await import('@/api')).group.reads.users_v3(
    userStore.class_id, options
)

const usersTab = reactive({
    page:     Number(route.query?.users_page     ?? 1),
    pageSize: Number(route.query?.users_pagesize ?? 10),
    query:    route.query?.users_query           ?? ''
})

const roleNames = {
    secretary: '团支书',
    department: '学生会',
    admin: '管理员',
    teacher: '教师',
}

const getRoles = async ({ query }) => (x => ({ data: x, total: x.length }))([
    'secretary', 'department', 'admin'
].filter(x => (roleNames[x] ?? x).indexOf(query) >= 0))

const rolesTab = reactive({
    query: route.query?.roles_query ?? ''
})

const getLogs = async ({pageSize, ...params}) => {
    const svc = await import('@/services/platform')
    return await svc.getLogs({
        size: pageSize,
        ...params,
        levels: logsTab.levels.toString(),
        user: logsTab.user,
        session: logsTab.session,
        net_session: logsTab.net_session,
    })
}

const logsTab = reactive({
    page:        Number(route.query?.logs_page     ?? 1),
    pageSize:    Number(route.query?.logs_pagesize ?? 10),
    query:       route.query?.logs_query           ?? '',
    levels:      (route.query?.logs_levels         ?? '').split(',').filter(x=>x),
    user:        route.query?.logs_user            ?? '',
    session:     route.query?.logs_session         ?? '',
    net_session: route.query?.logs_netsession      ?? '',
})

const getLegacyLogs = async ({
    page, pageSize, query
}) => {
    const api = await import('@/api')
    const { data, size } = await api.logs.read(page, pageSize, query)
    return { data, total: size }
}

const legacyLogs = reactive({
    page:     Number(route.query?.legacy_logs_page     ?? 1),
    pageSize: Number(route.query?.legacy_logs_pageSize ?? 10),
    query:    route.query?.legacy_logs_query           ?? ''
})

const tabVisibility = reactive({
    dashboard: false,
    classes: false,
    users: false,
    roles: false,
    logs: false,
    'legacy-logs': false
})

tabVisibility[pane.value] = true

const stats = reactive({
    classes:    { total: 0, loading: false },
    users:      { total: 0, loading: false },
    activities: { total: 0, loading: false },
})

const loadStats = async () => {
    const doLoad = async (key, fetcher) => {
        stats[key].loading = true
        try {
            const res = await fetcher()
            stats[key].total = Number(res?.total ?? 0)
        } catch {
            stats[key].total = 0
        } finally {
            stats[key].loading = false
        }
    }

    const api = await import('@/api')
    await Promise.all([
        doLoad('classes',    () => api.group.read_v2(1, 1, '')),
        doLoad('users',      () => api.user.read_v2({ page: 1, pageSize: 1, query: '' })),
        doLoad('activities', () => api.activity.read.campus_v3(1, 1, ''))
    ])
}

watch(() => tabVisibility.dashboard, v => { if (v) loadStats() }, { immediate: true })

const updateRoute = () => {
    tabVisibility[pane.value] = true
    router.replace({
        query: {
            tab: pane.value,
            classes_page:         classesTab.page,
            classes_pagesize:     classesTab.pageSize,
            classes_query:        classesTab.query,
            users_page:           usersTab.page,
            users_pagesize:       usersTab.pageSize,
            users_query:          usersTab.query,
            roles_query:          rolesTab.query,
            logs_page:            logsTab.page,
            logs_pagesize:        logsTab.pageSize,
            logs_query:           logsTab.query,
            logs_levels:          logsTab.levels.toString(),
            logs_user:            logsTab.user,
            logs_session:         logsTab.session,
            logs_netsession:      logsTab.net_session,
            legacy_logs_page:     legacyLogs.page,
            legacy_logs_pagesize: legacyLogs.pageSize,
            legacy_logs_query:    legacyLogs.query,
        }
    })
}

watch(pane,                      updateRoute)
watch(() => classesTab.page,     updateRoute)
watch(() => classesTab.pageSize, updateRoute)
watch(() => classesTab.query,    updateRoute)
watch(() => usersTab.page,       updateRoute)
watch(() => usersTab.pageSize,   updateRoute)
watch(() => usersTab.query,      updateRoute)
watch(() => rolesTab.query,      updateRoute)
watch(() => logsTab.page,        updateRoute)
watch(() => logsTab.pageSize,    updateRoute)
watch(() => logsTab.query,       updateRoute)
watch(() => logsTab.levels,      updateRoute)
watch(() => logsTab.user,        updateRoute)
watch(() => logsTab.session,     updateRoute)
watch(() => logsTab.net_session, updateRoute)
watch(() => legacyLogs.page,     updateRoute)
watch(() => legacyLogs.pageSize, updateRoute)
watch(() => legacyLogs.query,    updateRoute)

watch(() => route.query?.tab,                  v => { pane.value          = v        ?? defaultTab.value })
watch(() => route.query?.classes_page,         v => { classesTab.page     = Number(v ?? 1) })
watch(() => route.query?.classes_pagesize,     v => { classesTab.pageSize = Number(v ?? 10) })
watch(() => route.query?.classes_query,        v => { classesTab.query    = v        ?? '' })
watch(() => route.query?.users_page,           v => { usersTab.page       = Number(v ?? 1) })
watch(() => route.query?.users_pagesize,       v => { usersTab.pageSize   = Number(v ?? 10) })
watch(() => route.query?.users_query,          v => { usersTab.query      = v        ?? '' })
watch(() => route.query?.roles_query,          v => { rolesTab.query      = v        ?? '' })
watch(() => route.query?.logs_page,            v => { logsTab.page        = Number(v ?? 1) })
watch(() => route.query?.logs_pagesize,        v => { logsTab.pageSize    = Number(v ?? 10) })
watch(() => route.query?.logs_query,           v => { logsTab.query       = v        ?? '' })
watch(() => route.query?.logs_levels,          v => { logsTab.levels      = (v       ?? '').split(',').filter(x=>x) })
watch(() => route.query?.logs_user,            v => { logsTab.user        = v        ?? '' })
watch(() => route.query?.logs_session,         v => { logsTab.session     = v        ?? '' })
watch(() => route.query?.logs_netsession,      v => { logsTab.net_session = v        ?? '' })
watch(() => route.query?.legacy_logs_page,     v => { legacyLogs.page     = Number(v ?? 1) })
watch(() => route.query?.legacy_logs_pagesize, v => { legacyLogs.pagesize = Number(v ?? 10) })
watch(() => route.query?.legacy_logs_query,    v => { legacyLogs.query    = v        ?? '' })


const gotoAdmin = () => {
    confirm('确定离开此页面吗？') && (window.location = 'https://zvp.xiaobopisces.top:54188/zvp5bt')
}

const create = ref(false)
const creating = ref(false)
const cName = ref('')
const cYear = ref(undefined)
const classesVersion = ref(0)

const resetCreateForm = () => {
    cName.value = ''
    cYear.value = undefined
}

const doCreate = async () => {
    try {
        creating.value = true
        await (await import('@/api')).group.create_v3(cName.value, cYear.value)
        create.value = false
        classesVersion.value++
    } finally {
        creating.value = false
    }
}

const createUser = ref(false)
const creatingUser = ref(false)
const userId = ref('')
const userName = ref('')
const userRoles = ref(['student'])
const userClassId = ref('')
const classOptions = ref([])
const classOptionsLoading = ref(false)
const usersVersion = ref(0)

const roleOptions = [
    { value: 'student', label: '学生' },
    { value: 'secretary', label: roleNames.secretary },
    { value: 'department', label: roleNames.department },
    { value: 'admin', label: roleNames.admin },
    { value: 'teacher', label: roleNames.teacher },
]

const resetCreateUserForm = () => {
    userId.value = ''
    userName.value = ''
    userRoles.value = ['student']
    userClassId.value = ''
}

const loadClassOptions = async () => {
    classOptionsLoading.value = true
    try {
        const { data } = await (await import('@/api')).group.read_v2(1, 50, '')
        classOptions.value = data
    } finally {
        classOptionsLoading.value = false
    }
}

const onCreateUserOpen = () => {
    resetCreateUserForm()
    loadClassOptions()
}

const doCreateUser = async () => {
    try {
        creatingUser.value = true
        await (await import('@/api')).user.create_v3({
            id: userId.value,
            name: userName.value,
            roles: userRoles.value,
            class_id: userClassId.value || undefined
        })
        createUser.value = false
        usersVersion.value++
    } finally {
        creatingUser.value = false
    }
}

const reportsLoading = ref(false)

const downloadReports = async () => {
    try {
        reportsLoading.value = true
        const api = await import('@/api')
        const token = await api.exports.requestReports()
        const blob = await api.exports.downloadReports(token)
        api.exports.saveBlob(blob, 'export.tar.gz')
    } finally {
        reportsLoading.value = false
    }
}

const exporting = ref(false)

const pollExport = async (taskId) => {
    const api = await import('@/api')
    for (let i = 0; i < 60; i++) {
        const task = await api.exports.getExport(taskId)
        if (task?.status !== 'pending' && task?.status !== 'processing') return task
        await new Promise(r => setTimeout(r, 1000))
    }
    return null
}

const doExport = async (variant) => {
    try {
        exporting.value = true
        const api = await import('@/api')
        const taskId = variant === 'users'
            ? await api.exports.exportUsers()
            : await api.exports.exportTime({})
        const task = await pollExport(taskId)
        if (!task) return
        const blob = await api.exports.getExportFile(taskId)
        api.exports.saveBlob(blob, `zvms-export-${taskId}.xlsx`)
    } finally {
        exporting.value = false
    }
}
</script>

<template>
    <div class="manage-panel flex flex-col">
        <ElPageHeader class="manage-panel-header flex-shrink-0 noback" @back="$router.back()">
            <template #content>
                管理
            </template>
            <template #extra>
                <ElButton :icon="Plus" text bg type="success" v-if="isAdmin && pane == 'classes'" @click="create = true">
                    创建班级
                </ElButton>
                <ElButton :icon="Plus" text bg type="success" v-if="isAdmin && pane == 'users'" @click="createUser = true">
                    创建用户
                </ElButton>
                <ElButton text bg type="primary" v-if="isAdmin" :disabled="exporting" @click="doExport('users')">
                    导出用户表
                </ElButton>
                <ElButton text bg type="primary" v-if="isAdmin" :disabled="exporting" @click="doExport('time')">
                    导出时长表
                </ElButton>
                <ElButton text bg type="primary" v-if="isAdmin" :disabled="reportsLoading" @click="downloadReports()">
                    下载报表
                </ElButton>
                <ElButton :icon="Key" text bg type="danger" v-if="isAdmin" @click="gotoAdmin">
                    前往管理后台
                </ElButton>
            </template>
        </ElPageHeader>
        <ElDialog
            v-model="create"
            @open="resetCreateForm()"
            :close-on-click-modal="false"
            align-center
            :show-close="!creating"
            :close-on-press-escape="!creating"
        >
            <template #header>
                创建班级
            </template>
            <ElForm v-loading="creating">
                <ElFormItem label="班级名">
                    <ElInput placeholder="请输入" v-model="cName"></ElInput>
                </ElFormItem>
                <ElFormItem label="入学年份">
                    <ElInputNumber placeholder="可以为空" v-model="cYear" :min="2000" :max="2100"></ElInputNumber>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doCreate()"
                    :disabled="creating||!cName"
                >
                    创建
                </ElButton>
            </template>
        </ElDialog>
        <ElDialog
            v-model="createUser"
            @open="onCreateUserOpen()"
            :close-on-click-modal="false"
            align-center
            :show-close="!creatingUser"
            :close-on-press-escape="!creatingUser"
        >
            <template #header>
                创建用户
            </template>
            <ElForm v-loading="creatingUser">
                <ElFormItem label="学号">
                    <ElInput placeholder="请输入" v-model="userId"></ElInput>
                </ElFormItem>
                <ElFormItem label="姓名">
                    <ElInput placeholder="请输入" v-model="userName"></ElInput>
                </ElFormItem>
                <ElFormItem label="角色">
                    <ElSelect multiple placeholder="请选择" v-model="userRoles">
                        <ElOption
                            v-for="r in roleOptions"
                            :key="r.value"
                            :label="r.label"
                            :value="r.value"
                        ></ElOption>
                    </ElSelect>
                </ElFormItem>
                <ElFormItem label="班级">
                    <ElSelect
                        clearable
                        placeholder="可以为空"
                        v-model="userClassId"
                        :loading="classOptionsLoading"
                    >
                        <ElOption
                            v-for="c in classOptions"
                            :key="c._id"
                            :label="c.name"
                            :value="c._id"
                        ></ElOption>
                    </ElSelect>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doCreateUser()"
                    :disabled="creatingUser||!userId||!userName"
                >
                    创建
                </ElButton>
            </template>
        </ElDialog>
        <ElTabs
            v-if="canView" type="card" class="manage-tabs el-tabs--flex-pane" :default-value="pane" v-model="pane"
        >
            <ElTabPane label="总览" name="dashboard" class="flex flex-col pane dashboard-pane" v-if="isAdmin">
                <template v-if="pane=='dashboard'">
                    <ElDivider content-position="left">
                        <span class="section-title">数据概览</span>
                    </ElDivider>
                    <ElRow :gutter="16" class="stats-row">
                        <ElCol :span="8" :xs="24">
                            <ElCard shadow="never" v-loading="stats.classes.loading" class="stat-card">
                                <ElStatistic title="班级总数" :value="stats.classes.total" />
                            </ElCard>
                        </ElCol>
                        <ElCol :span="8" :xs="24">
                            <ElCard shadow="never" v-loading="stats.users.loading" class="stat-card">
                                <ElStatistic title="用户总数" :value="stats.users.total" />
                            </ElCard>
                        </ElCol>
                        <ElCol :span="8" :xs="24">
                            <ElCard shadow="never" v-loading="stats.activities.loading" class="stat-card">
                                <ElStatistic title="活动总数" :value="stats.activities.total" />
                            </ElCard>
                        </ElCol>
                    </ElRow>
                    <ElDivider content-position="left">
                        <span class="section-title">动态登录码</span>
                    </ElDivider>
                    <ZDisplayTOTP></ZDisplayTOTP>
                </template>
                <template v-if="tabVisibility.dashboard">

                </template>
            </ElTabPane>
            <ElTabPane label="班级" name="classes" class="flex flex-col pane" v-if="!isRestricted">
                <ZList
                    v-if="tabVisibility.classes"
                    :key="'classes-' + classesVersion"
                    :remote-method="getClasses"
                    v-model:page="classesTab.page"
                    v-model:page-size="classesTab.pageSize"
                    v-model:query="classesTab.query"
                    search-button
                    v-slot="{ item, refresh }"
                >
                    <ZClassDisplay :group="item" @updated="refresh()"></ZClassDisplay>
                </ZList>
            </ElTabPane>
            <ElTabPane label="用户" name="users" class="flex flex-col pane">
                <ZList
                    v-if="tabVisibility.users"
                    :key="'users-' + usersVersion"
                    :remote-method="isRestricted ? restrictedGetUsers : getUsers"
                    v-model:page="usersTab.page"
                    v-model:page-size="usersTab.pageSize"
                    v-model:query="usersTab.query"
                    search-button
                    v-slot="{ item }"
                >
                    <ZUserDisplay :user="item"></ZUserDisplay>
                </ZList>
            </ElTabPane>
            <ElTabPane label="权限" name="roles" class="flex flex-col pane" v-if="isAdmin">
                <ZList
                    v-if="tabVisibility.roles"
                    :remote-method="getRoles"
                    :page="1"
                    :page-size="1000"
                    v-model:query="rolesTab.query"
                    search-button
                    :pageable="false"
                    v-slot="{ item }"
                >
                    <ZRoleDisplay :role="item"></ZRoleDisplay>
                </ZList>
            </ElTabPane>
            <ElTabPane label="日志" name="logs" class="flex flex-col pane" v-if="isAdmin">
                <ZList
                    v-if="tabVisibility.logs"
                    :remote-method="getLogs"
                    v-model:page="logsTab.page"
                    v-model:page-size="logsTab.pageSize"
                    v-model:query="logsTab.query"
                    search-button gap
                >
                    <template #filters="{ refresh }">
                        <ElSelect multiple v-model="logsTab.levels" style="flex-grow:1" @change="refresh" placeholder="错误,警告,普通" clearable>
                            <ElOption label="错误" value="error"></ElOption>
                            <ElOption label="警告" value="warning"></ElOption>
                            <ElOption label="普通" value="info"></ElOption>
                            <ElOption label="详细" value="verbose"></ElOption>
                            <ElOption label="调试" value="debug"></ElOption>
                            <template #tag><span style="margin-left:8px;" class="el-select__placeholder">{{logsTab.levels.map(x=>(logLevels[x]??x)).toString()||'\ufeff'}}</span></template>
                        </ElSelect>
                        <ElInput style="flex-grow:1" placeholder="用户ID" readonly v-model="logsTab.user">
                            <template #suffix>
                                <ElIcon v-show="logsTab.user" @click="logsTab.user='';refresh()">
                                    <CircleClose></CircleClose>
                                </ElIcon>
                            </template>
                        </ElInput>
                        <ElInput style="flex-grow:1" placeholder="会话ID" readonly v-model="logsTab.session">
                            <template #suffix>
                                <ElIcon v-show="logsTab.session" @click="logsTab.session='';refresh()">
                                    <CircleClose></CircleClose>
                                </ElIcon>
                            </template>
                        </ElInput>
                        <ElInput style="flex-grow:1" placeholder="设备ID" readonly v-model="logsTab.net_session">
                            <template #suffix>
                                <ElIcon v-show="logsTab.net_session" @click="logsTab.net_session='';refresh()">
                                    <CircleClose></CircleClose>
                                </ElIcon>
                            </template>
                        </ElInput>
                    </template>
                    <template #default="{ item, refresh }">
                        <ZLogDisplay :log="item" @track="({key,value})=>{if(logsTab[key]!==value){logsTab[key]=value;refresh()}}"></ZLogDisplay>
                    </template>
                </ZList>
            </ElTabPane>
            <ElTabPane label="日志 (旧版)" name="legacy-logs" class="flex flex-col pane" v-if="isAdmin">
                <ZList
                    v-if="tabVisibility['legacy-logs']"
                    :remote-method="getLegacyLogs"
                    v-model:page="legacyLogs.page"
                    v-model:page-size="legacyLogs.pageSize"
                    v-model:query="legacyLogs.query"
                    search-button gap
                    v-slot="{ item }"
                >
                    <ZLogCard :log="item" style="flex-grow: 1; flex-shrink: 1"></ZLogCard>
                </ZList>
            </ElTabPane>
        </ElTabs>
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

.stats-row {
    width: 100%;
}

.stat-card {
    margin-bottom: 1rem;
}
</style>
