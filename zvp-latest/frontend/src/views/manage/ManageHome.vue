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
    ElFormItem
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

const devNotice = () => alert('功能开发中')
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
                <ElButton :icon="Plus" text bg type="success" v-if="isAdmin && pane == 'users'" @click="devNotice()">
                    创建用户
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
        <ElTabs
            v-if="canView" type="card" class="manage-tabs el-tabs--flex-pane" :default-value="pane" v-model="pane"
        >
            <ElTabPane label="总览" name="dashboard" class="flex flex-col pane dashboard-pane" v-if="isAdmin">
                <template v-if="pane=='dashboard'">
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
</style>
