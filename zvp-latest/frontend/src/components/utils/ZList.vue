<script setup>
import { ref, watch, toRefs, onMounted, computed } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { ElInput, ElButton, ElPagination } from 'element-plus'

const props = defineProps({
    remoteMethod: { type: Function, required: true },
    page: { type: Number, default: 1 },
    pageSize: { type: Number, default: 10 },
    query: { type: String, default: '' },
    searchButton: { type: Boolean, default: false },
    gap: { type: Boolean, default: false },
    pageable: { type: Boolean, default: true },
    searchable: { type: Boolean, default: true },
})

const emits = defineEmits(['update:page', 'update:pageSize', 'update:query'])

const { remoteMethod, page, pageSize, query, searchButton, gap, pageable, searchable } = toRefs(props)

const loading = ref(true)
const loaded = ref(false)

const _page = ref(page.value)
const _pageSize = ref(pageSize.value)
const _query = ref(query.value)

const items = ref([])
const itemCount = ref(0)

const refresh = async () => {
    loading.value = true
    try {
        const { data, total } = await remoteMethod.value({
            page: page.value,
            pageSize: pageSize.value,
            query: query.value,
        })
        if (!data.length && page.value !== 1) {
            _page.value = Math.floor(Math.max(data.length - 1, 0) / pageSize.value) + 1
        } else {
            items.value = data
            itemCount.value = total
            _page.value = page.value
            _pageSize.value = pageSize.value
            _query.value = query.value
        }
    } catch(_) {}
    loading.value = false
    loaded.value = true
}

watch(remoteMethod, refresh)
watch(page, refresh)
watch(pageSize, refresh)
watch(query, refresh)

watch(_page, () => (page.value !== _page.value) && emits('update:page', _page.value))
watch(_pageSize, () => (pageSize.value !== _pageSize.value) && emits('update:pageSize', _pageSize.value))

const updateQuery = () => (query.value !== _query.value) && emits('update:query', _query.value)

const isSearch = computed(() => searchButton.value && query.value !== _query.value)

onMounted(refresh)
</script>

<template>
    <div :class="['list-container', 'flex', 'flex-col', (gap ? 'is-gap' : '')]" v-loading="loading">
        <div v-if="$slots.filters" class="list-header flex flex-row flex-shrink-0">
            <slot name="filters" :refresh="refresh"></slot>
        </div>
        <div v-if="searchable" class="list-header flex flex-row flex-shrink-0">
            <ElInput
                class="flex-grow"
                v-model="_query"
                placeholder="搜索……"
                @blur="!searchButton && updateQuery()"
                @keydown.enter="updateQuery()"
            ></ElInput>
            <ElButton
                text bg
                class="flex-shrink-0"
                :type="isSearch ? 'success' : 'primary'"
                :icon="isSearch ? Search : Refresh"
                @click="isSearch ? updateQuery() : refresh()"
            >{{ isSearch ? '搜索' : '刷新' }}</ElButton>
        </div>
        <div class="list-body flex flex-col flex-grow">
            <template v-if="items.length">
                <div v-for="item in items" class="list-item flex flex-row flex-shrink-0">
                    <slot :item="item" :refresh="refresh"></slot>
                </div>
            </template>
            <div v-else-if="loaded" class="list-empty flex flex-col flex-grow">
                <slot name="empty">
                    没有内容
                </slot>
            </div>
        </div>
        <ElPagination
            v-if="loaded && pageable"
            class="list-pager flex-shrink-0"
            background
            layout="->, total, prev, pager, next, sizes, jumper"
            :pager-count="3"
            :page-sizes="[5, 10, 15, 20]"
            :total="itemCount"
            v-model:current-page="_page"
            v-model:page-size="_pageSize"
        >
        </ElPagination>
    </div>
</template>

<style scoped>
.list-container, .list-header {
    gap: .5rem;
    overflow: hidden;
}

.list-body {
    overflow-y: scroll;
}

.list-container.is-gap .list-body {
    /*gap: .5rem;*/
}

.list-item {
    padding: .5rem;
    gap: .75rem;
    overflow: hidden;
    align-items: center;
}

.list-container:not(.is-gap) .list-item + .list-item {
    border-top: var(--el-border);
}

.list-empty {
    align-items: center;
    justify-content: center;
    gap: .5rem;
    flex-grow: 1;
    color: var(--el-text-color-placeholder);
}

.list-body, .list-container {
    flex-grow: 1;
}
</style>