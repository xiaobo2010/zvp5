<script setup lang="ts">
import type { Log } from '@/../types/log'
import { ref, watch, toRefs } from 'vue'
import api from '@/api'
import ZLogCard from '@/components/log/ZLogCard.vue'
import Skeleton from '@/ui/ux/Skeleton.vue'
import { ElInput } from 'element-plus'

const props = withDefaults(
  defineProps<{
    user: string
  }>(),
  {
    user: ''
  }
)

const { user } = toRefs(props)

const logs = ref<Log[]>([])
const loading = ref(false)
const page = ref(1)
const perpage = ref(10)
const query = ref('')
const query2 = ref('')
const xuehai_id = ref('')
const xuehai_id2 = ref('')
const amount = ref(0)

async function refresh() {
  loading.value = true
  try {
    const result = await api.logs.read(
      page.value,
      perpage.value,
      query.value,
      user.value,
      xuehai_id.value
    )
    logs.value = result?.data ?? []
    amount.value = result?.size ?? 0
  } finally {
    loading.value = false
  }
}
refresh()

watch(page, refresh)
watch(perpage, refresh)
watch(query2, refresh)
watch(xuehai_id2, refresh)
</script>

<template>
  <div>
    <div class="yg2-toolbar">
      <ElInput
        v-model.lazy="query"
        class="yg2-toolbar__grow"
        placeholder="搜索日志内容"
        clearable
        @keydown.enter="query2 = query"
        @blur="query2 = query"
      />
      <ElInput
        v-model.lazy="xuehai_id"
        class="yg2-toolbar__field"
        placeholder="学海 ID"
        clearable
        @keydown.enter="xuehai_id2 = xuehai_id"
        @blur="xuehai_id2 = xuehai_id"
      />
    </div>
    <div v-if="loading && logs.length === 0" class="flex flex-col gap-2 py-1">
      <Skeleton v-for="i in 3" :key="i" height="96px" radius="var(--yg2-r-12)" />
    </div>
    <template v-else>
      <ZLogCard v-for="log in logs" :key="log._id" :log="log" />
    </template>
    <div class="py-2">
      <ZPagination
        v-model:current-page="page"
        v-model:page-size="perpage"
        :total="amount"
      />
    </div>
  </div>
</template>

<style scoped></style>
