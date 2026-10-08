<script setup lang="ts">
import { ref, watch, reactive, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ZList from '@/components/utils/ZList.vue'
import ZActivityDisplay from '@/components/activity/ZActivityDisplay.vue'
import { Plus, ArrowRight } from '@element-plus/icons-vue'
import { useUserStore, useState } from '@/stores'
import { ElPageHeader, ElTabs, ElTabPane, ElButton, ElDialog, ElForm, ElFormItem, ElSelect, ElOption, ElInput, ElIcon } from 'element-plus'

const state = useState()

const user = useUserStore()

const route = useRoute()
const router = useRouter()


const getMyActivities = async ({
    page, pageSize, query
}) => {
    const api = await import('@/api')
    const { activities, total } = await api.activity.read.mine_v3(
        user._id, page, pageSize, query
    )
    return { data: activities, total }
}

const getClassActivities = async ({
    page, pageSize, query
}) => {
    const api = await import('@/api')
    const { activities, total } = await api.activity.read.class_v3(
        user.class_id, page, pageSize, query
    )
    return { data: activities, total }
}

const getSchoolActivities = async ({
    page, pageSize, query
}) => {
    const api = await import('@/api')
    const { activities, total } = await api.activity.read.campus_v3(
        page, pageSize, query
    )
    return { data: activities, total }
}

const pane = ref(route.query?.tab ?? 'mine')

const myActivities = reactive({
    page:     Number(route.query?.my_page     ?? 1),
    pageSize: Number(route.query?.my_pagesize ?? 10),
    query:    route.query?.my_query           ?? ''
})

const classActivities = reactive({
    page:     Number(route.query?.class_page     ?? 1),
    pageSize: Number(route.query?.class_pagesize ?? 10),
    query:    route.query?.class_query           ?? ''
})

const schoolActivities = reactive({
    page:     Number(route.query?.school_page     ?? 1),
    pageSize: Number(route.query?.school_pagesize ?? 10),
    query:    route.query?.school_query           ?? ''
})

const tabVisibility = reactive({
    mine: false,
    'class': false,
    school: false
})

tabVisibility[pane.value] = true


const updateRoute = () => {
    tabVisibility[pane.value] = true
    router.replace({
        query: {
            tab:             pane.value,
            my_page:         myActivities.page,
            my_pagesize:     myActivities.pageSize,
            my_query:        myActivities.query,
            class_page:      classActivities.page,
            class_pagesize:  classActivities.pageSize,
            class_query:     classActivities.query,
            school_page:     schoolActivities.page,
            school_pagesize: schoolActivities.pageSize,
            school_query:    schoolActivities.query,
        }
    })
}

watch(pane,                            updateRoute)
watch(() => myActivities.page,         updateRoute)
watch(() => myActivities.pageSize,     updateRoute)
watch(() => myActivities.query,        updateRoute)
watch(() => classActivities.page,      updateRoute)
watch(() => classActivities.pageSize,  updateRoute)
watch(() => classActivities.query,     updateRoute)
watch(() => schoolActivities.page,     updateRoute)
watch(() => schoolActivities.pageSize, updateRoute)
watch(() => schoolActivities.query,    updateRoute)

watch(() => route.query?.tab,             v => { pane.value                = v        ?? 'mine'})
watch(() => route.query?.my_page,         v => { myActivities.page         = Number(v ?? 1)})
watch(() => route.query?.my_pagesize,     v => { myActivities.pageSize     = Number(v ?? 10)})
watch(() => route.query?.my_query,        v => { myActivities.query        = v        ?? ''})
watch(() => route.query?.class_page,      v => { classActivities.page      = Number(v ?? 1)})
watch(() => route.query?.class_pagesize,  v => { classActivities.pageSize  = Number(v ?? 10)})
watch(() => route.query?.class_query,     v => { classActivities.query     = v        ?? ''})
watch(() => route.query?.school_page,     v => { schoolActivities.page     = Number(v ?? 1)})
watch(() => route.query?.school_pagesize, v => { schoolActivities.pageSize = Number(v ?? 10)})
watch(() => route.query?.school_query,    v => { schoolActivities.query    = v        ?? ''})

const create = ref(false)
const creating = ref(false)

const cName = ref('')
const cDesc = ref('')
const cType = ref('')

const resetForm = () => {
    cName.value = ''
    cDesc.value = ''
    cType.value = ''
}

const doCreate = async () => {
    creating.value = true
    try {
        const axios = (await import('@/plugins/axios')).default
        const { data: { id } } = await axios('/v3/activities', {
            method: 'POST',
            data: {
                name: cName.value,
                description: cDesc.value,
                type: cType.value
            }
        })
        state.set('unsaved', false)
        router.push(`/activity/details/${id}`)
    } catch(_) {
        creating.value = false
    }
}

watch(create, x => state.set('unsaved', x))
onUnmounted(() => state.set('unsaved', false))

</script>

<template>
    <div class="activities-panel flex flex-col">
        <ElDialog
            v-model="create"
            @open="resetForm()"
            :close-on-click-modal="false"
            align-center
            :show-close="!creating"
            :close-on-press-escape="!creating"
        >
            <template #header>
                创建义工
            </template>
            <ElForm v-loading="creating">
                <ElFormItem label="名称">
                    <ElInput placeholder="请输入" v-model="cName"></ElInput>
                </ElFormItem>
                <ElFormItem label="描述">
                    <ElInput placeholder="可以为空" type="textarea" v-model="cDesc"></ElInput>
                </ElFormItem>
                <ElFormItem label="类型">
                    <ElSelect v-model="cType">
                        <ElOption value="on-campus" label="校内义工"></ElOption>
                        <ElOption value="off-campus" label="校外义工"></ElOption>
                        <ElOption value="social-practice" label="社会实践"></ElOption>
                        <ElOption value="hybrid" label="特殊义工"></ElOption>
                    </ElSelect>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doCreate()"
                    :disabled="creating||!cName||!cType"
                >
                    立即创建
                    <ElIcon class="el-icon--right">
                        <ArrowRight></ArrowRight>
                    </ElIcon>
                </ElButton>
            </template>
        </ElDialog>
        <ElPageHeader class="activities-panel-header flex-shrink-0 noback" @back="$router.back()">
            <template #content>
                义工列表
            </template>
            <template #extra>
                <ElButton
                    v-if="user.position.includes('admin') || user.position.includes('department')"
                    type="success"
                    :icon="Plus"
                    text bg
                    @click="/*$router.push('/activity/create')*/create=true"
                >
                    创建义工
                </ElButton>
            </template>
        </ElPageHeader>
        <ElTabs type="card" class="activities-tabs el-tabs--flex-pane" :default-value="pane" v-model="pane">
            <ElTabPane label="我的" name="mine" class="flex flex-col pane">
                <ZList
                    v-if="tabVisibility.mine"
                    :remote-method="getMyActivities"
                    v-model:page="myActivities.page"
                    v-model:page-size="myActivities.pageSize"
                    v-model:query="myActivities.query"
                    search-button
                    v-slot="{ item }"
                >
                    <ZActivityDisplay :activity="item"></ZActivityDisplay>
                </ZList>
            </ElTabPane>
            <ElTabPane label="班级" name="class" class="flex flex-col pane" v-if="
                (user.position.includes('secretary') || user.position.includes('admin')) && user.class_id
            ">
                <ZList
                    v-if="tabVisibility['class']"
                    :remote-method="getClassActivities"
                    v-model:page="classActivities.page"
                    v-model:page-size="classActivities.pageSize"
                    v-model:query="classActivities.query"
                    search-button
                    v-slot="{ item }"
                >
                    <ZActivityDisplay :activity="item"></ZActivityDisplay>
                </ZList>
            </ElTabPane>
            <ElTabPane label="全校" name="school" class="flex flex-col pane" v-if="
                user.position.includes('department') || user.position.includes('admin')
            ">
                <ZList
                    v-if="tabVisibility.school"
                    :remote-method="getSchoolActivities"
                    v-model:page="schoolActivities.page"
                    v-model:page-size="schoolActivities.pageSize"
                    v-model:query="schoolActivities.query"
                    search-button
                    v-slot="{ item }"
                >
                    <ZActivityDisplay :activity="item"></ZActivityDisplay>
                </ZList>
            </ElTabPane>
        </ElTabs>
    </div>
</template>

<style scoped>
.activities-panel {
    padding: 2rem;
    gap: 2rem;
    background: var(--el-bg-color);
}

.activities-tabs, .activities-tabs .pane {
    flex-grow: 1;
    flex-shrink: 1;
    overflow: hidden;
}
</style>
