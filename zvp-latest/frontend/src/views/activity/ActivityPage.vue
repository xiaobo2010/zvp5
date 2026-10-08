<script setup lang="ts">
import { ref, onMounted, watch, computed, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ZList from '@/components/utils/ZList.vue'
import ZActivityMemberDisplay from '@/components/activity/ZActivityMemberDisplay.vue'
import { useState, useUserStore } from '@/stores'
import { Plus, Edit, Delete, ArrowRight } from '@element-plus/icons-vue'
import {
    ElDivider,
    ElButton,
    ElPageHeader,
    ElDescriptions,
    ElDescriptionsItem,
    ElMessageBox,
    ElDialog,
    ElForm,
    ElInput,
    ElInputNumber,
    ElFormItem,
    ElSelect,
    ElOption,
    ElIcon
} from 'element-plus'
import dayjs from '@/plugins/dayjs'
import { Audit } from '@icon-park/vue-next'

const modeNames = {
    'on-campus': '校内义工',
    'off-campus': '校外义工',
    'social-practice': '社会实践'
}

const statusNames = {
    pending: '审核中',
    effective: '有效',
    refused: '无效',
    draft: '草稿', // reserved
}

const route = useRoute()
const router = useRouter()
const state = useState()

const user = useUserStore()

const _id = route.params?.id as string
const session = route.query?.session as string

const id = ref(route.params?.id)
const activity = ref(null)

const getActivity = async () => {
    try {
        activity.value = (await (await import('@/api')).activity.read.single(id.value)).activity
    } catch(_) {
        router.back()
    }
}

const getMembers = async ({ page, pageSize, query}) => {
    const { members, total } = await (await import('@/api')).activity.member.reads_v3(id.value, page, pageSize, query)
    return { data: members, total }
}

const deleteActivity = async () => {
    await (await import('@/api')).activity.deleteOne(id.value, user._id)
    router.back()
}

const auditActions = {
    effective: {
        buttonClass: 'el-button--success',
        buttonText: '通过'
    },
    pending: {
        buttonClass: 'el-button--primary',
        buttonText: '打回'
    },
    refused: {
        buttonClass: 'el-button--danger',
        buttonText: '拒绝'
    }
}

const auditActionSets = {
    effective: {
        confirm: 'pending',
        cancel: 'refused'
    },
    pending: {
        confirm: 'effective',
        cancel: 'refused'
    },
    refused: {
        confirm: 'effective',
        cancel: 'pending'
    }
}

const auditActivity = async () => {
    const api = await import('@/api')
    const actionSet = auditActionSets[activity.value?.status]
    if(!actionSet) return
    const confirmAct = auditActions[actionSet.confirm]
    const cancelAct = auditActions[actionSet.cancel]
    try {
        await ElMessageBox({
            title: '审核义工',
            message: '请选择操作',
            confirmButtonText: confirmAct.buttonText,
            confirmButtonClass: confirmAct.buttonClass,
            cancelButtonText: cancelAct.buttonText,
            cancelButtonClass: cancelAct.buttonClass,
            showCancelButton: true,
            distinguishCancelAndClose: true,
            closeOnClickModal: false
        })
        await api.activity.update.status(activity.value._id, actionSet.confirm)
        await getActivity()
    } catch(act) {
        if(act === 'cancel') {
            await api.activity.update.status(id.value, actionSet.cancel)
            await getActivity()
        }
    }
}

const page = ref(Number(route.query?.page ?? 1))
const pageSize = ref(Number(route.query?.pagesize ?? 10))
const query = ref(route.query?.query ?? '')

const hasPerm = computed(() => user.position.includes('admin') || user.position.includes('department'))
const canAudit = computed(() => activity.value?.status === 'pending' ? hasPerm.value : user.position.includes('admin'))

watch(() => route.params?.id, v => { id.value = v })

const updateRoute = () => {
    router.replace({
        query: {
            page: page.value,
            pagesize: pageSize.value,
            query: query.value
        }
    })
}

watch(page, updateRoute)
watch(pageSize, updateRoute)
watch(query, updateRoute)

watch(() => route.query?.page, v => { page.value = Number(v) })
watch(() => route.query?.pagesize, v => { pageSize.value = Number(v) })
watch(() => route.query?.query, v => { query.value = v })

const edit = ref(false)
const editing = ref(false)
const eName = ref('')
const eDesc = ref('')

const resetForm = () => {
    eName.value = activity.value?.name ?? ''
    eDesc.value = activity.value?.description ?? ''
}

const doEdit = async () => {
    try {
        editing.value = true
        const api = await import('@/api')
        await api.activity.update.info(
            id.value,
            eName.value,
            eDesc.value
        )
        await getActivity()
        edit.value = false
    } finally {
        editing.value = false
    }
    // TODO WIP
}

const add = ref(false)
const adding = ref(false)
const rows = ref([])
const userOptions = ref([])
const userLoading = ref(false)
const membersVersion = ref(0)

const newRow = () => ({
    user: '',
    mode: activity.value?.type === 'hybrid' ? '' : (activity.value?.type ?? ''),
    duration: 1
})

const resetAddForm = () => {
    rows.value = [newRow()]
    userOptions.value = []
}

const addFormValid = computed(
    () => rows.value.length && rows.value.every(r => r.user && r.mode && r.duration > 0)
)

const searchUsers = async (query) => {
    userLoading.value = true
    try {
        const { data } = await (await import('@/api')).user.read_v2({ page: 1, pageSize: 20, query })
        userOptions.value = data
    } finally {
        userLoading.value = false
    }
}

const addRow = () => { rows.value.push(newRow()) }
const removeRow = (i) => { rows.value.splice(i, 1) }

const doAdd = async () => {
    try {
        adding.value = true
        await (await import('@/api')).activity.member.insertMany(
            id.value,
            rows.value.map(r => ({ member: r.user, mode: r.mode, duration: Number(r.duration) }))
        )
        add.value = false
        membersVersion.value++
    } finally {
        adding.value = false
    }
}

watch(id, getActivity)
onMounted(getActivity)

watch(edit, x => state.set('unsaved', x))
onUnmounted(() => state.set('unsaved', false))

</script>

<template>
    <div class="activity-panel flex flex-col">
        <ElDialog
            v-model="edit"
            @open="resetForm()"
            :close-on-click-modal="false"
            align-center
            :show-close="!editing"
            :close-on-press-escape="!editing"
        >
            <template #header>
                修改义工
            </template>
            <ElForm v-loading="editing">
                <ElFormItem label="名称">
                    <ElInput placeholder="请输入" v-model="eName"></ElInput>
                </ElFormItem>
                <ElFormItem label="描述">
                    <ElInput placeholder="可以为空" type="textarea" v-model="eDesc"></ElInput>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doEdit()"
                    :disabled="editing||!eName"
                >
                    修改
                    <ElIcon class="el-icon--right"><ArrowRight></ArrowRight></ElIcon>
                </ElButton>
            </template>
        </ElDialog>
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
            <ElForm v-loading="adding" label-position="top">
                <ElFormItem
                    v-for="(row, i) in rows"
                    :key="i"
                    :label="`成员 ${i + 1}`"
                >
                    <div class="add-row flex flex-row">
                        <ElSelect
                            class="flex-grow"
                            v-model="row.user"
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
                        <ElSelect
                            class="flex-shrink-0"
                            v-model="row.mode"
                            :disabled="activity?.type !== 'hybrid'"
                            placeholder="类型"
                        >
                            <ElOption
                                v-for="(name, value) in modeNames"
                                :key="value"
                                :label="name"
                                :value="value"
                            ></ElOption>
                        </ElSelect>
                        <ElInputNumber
                            class="flex-shrink-0"
                            v-model="row.duration"
                            :min="0.5"
                            :step="0.5"
                        ></ElInputNumber>
                        <ElButton
                            class="flex-shrink-0"
                            text bg type="danger"
                            :icon="Delete"
                            :disabled="adding"
                            @click="removeRow(i)"
                        ></ElButton>
                    </div>
                </ElFormItem>
                <ElFormItem>
                    <ElButton text bg :icon="Plus" :disabled="adding" @click="addRow()">添加一行</ElButton>
                </ElFormItem>
            </ElForm>
            <template #footer>
                <ElButton
                    text bg
                    type="primary"
                    @click="doAdd()"
                    :disabled="adding || !addFormValid"
                >
                    添加
                    <ElIcon class="el-icon--right"><ArrowRight></ArrowRight></ElIcon>
                </ElButton>
            </template>
        </ElDialog>
        <ElPageHeader style="margin-bottom:1.5rem" @back="$router.back()">
            <template #content>
                义工详情
            </template>
            <template #extra>
                <ElButton
                    v-if="hasPerm"
                    text bg
                    :icon="Plus"
                    type="success"
                    @click="add = true"
                >
                    添加成员
                </ElButton>
                <ElButton
                    v-if="hasPerm"
                    text bg
                    :icon="Edit"
                    type="warning"
                    @click="edit = true"
                >
                    修改
                </ElButton>
                <ElButton
                    v-if="canAudit"
                    text bg
                    :icon="Audit"
                    type="primary"
                    @click="auditActivity()"
                >
                    审核
                </ElButton>
                <ElButton
                    v-if="hasPerm"
                    text bg
                    :icon="Delete"
                    type="danger"
                    @click="deleteActivity()"
                >
                    删除
                </ElButton>
            </template>
        </ElPageHeader>
        <div class="pre-wrap activity-name">{{activity?.name || '\ufeff'}}</div>
        <pre class="pre-wrap activity-desc">{{activity?.description || '\ufeff'}}</pre>
        <ElDescriptions :column="2" v-loading="!activity?._id">
            <ElDescriptionsItem>
                <template #label>
                    类型
                </template>
                {{activity && (modeNames[activity.type] ?? '特殊义工')}}
            </ElDescriptionsItem>
            <ElDescriptionsItem>
                <template #label>
                    日期
                </template>
                {{activity && dayjs(activity.date).format('YYYY-MM-DD')}}
            </ElDescriptionsItem>
            <ElDescriptionsItem>
                <template #label>
                    状态
                </template>
                {{activity && (statusNames[activity.status] ?? '未知状态')}}
            </ElDescriptionsItem>
        </ElDescriptions>
        <ElDivider content-position="left">
            <span class="section-title">成员列表</span>
        </ElDivider>
        <ZList
            :key="id + '-' + membersVersion"
            :remote-method="getMembers"
            v-model:page="page"
            v-model:page-size="pageSize"
            v-model:query="query"
            search-button
            v-slot="{ item, refresh }"
        >
            <ZActivityMemberDisplay
                :activity-id="id"
                :member="item"
                :status-override="(activity?.status ?? item.status)"
                @updated="refresh()"
            ></ZActivityMemberDisplay>
        </ZList>
    </div>
</template>

<style scoped>
.activity-panel {
    background: var(--el-bg-color);
    gap: .5rem;
    padding: 2rem;
    overflow-y: hidden;
}

.section-title {
    font-size: large;
    font-weight: bold;
}

.add-row {
    gap: .5rem;
    align-items: center;
    flex-grow: 1;
}

.notice {
    font-weight: bold;
    color: var(--el-color-danger);
}

.pre-wrap {
    white-space: pre-wrap;
    overflow-wrap: break-word;
    word-break: break-all;
    overflow-y: scroll;
    flex-shrink: 0;
    margin: 0;
    padding: 0;
}

.activity-name {
    max-height: 1lh;
    font-size: large;
    font-weight: bold;
}

.activity-desc {
    max-height: 25%;
}
</style>