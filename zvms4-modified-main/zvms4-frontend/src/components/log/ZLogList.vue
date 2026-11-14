<script setup lang="ts">
import type { Log } from '@/../types/log'
import { ref, watch, toRefs } from 'vue'
import api from '@/api'
import ZLogCard from '@/components/log/ZLogCard.vue'
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
  const result = await api.logs.read(
    page.value,
    perpage.value,
    query.value,
    user.value,
    xuehai_id.value
  )
  logs.value = result?.data ?? []
  amount.value = result?.size ?? 0
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
    <ZLogCard v-loading="loading" :log="log" v-for="log in logs" :key="log._id" />
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
