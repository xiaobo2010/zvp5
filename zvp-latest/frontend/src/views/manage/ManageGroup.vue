<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { ref, watch, computed, reactive, onMounted } from 'vue'
import ZList from '@/components/utils/ZList.vue'
import ZUserDisplay from '@/components/user/ZUserDisplay.vue'
import {
    ElPageHeader
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
        </ElPageHeader>
                <ZList
                    v-if="hasPerm"
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
