<script setup lang="ts">
import type { Log } from '@/../types/log'
import { ref, watch, toRefs } from 'vue'
import api from '@/api'
import ZLogCard from '@/components/log/ZLogCard.vue'
import { ElInput } from 'element-plus/es/components/input/index.mjs'

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

// 请求序号守卫：四个 watch 都可能连发，慢响应不得覆盖后发请求的结果
let requestSeq = 0

async function refresh() {
  const seq = ++requestSeq
  loading.value = true
  try {
    const result = await api.logs.read(
      page.value,
      perpage.value,
      query.value,
      user.value,
      xuehai_id.value
    )
    if (seq !== requestSeq) return
    logs.value = result?.data ?? []
    amount.value = result?.size ?? 0
  } catch {
    if (seq !== requestSeq) return
    logs.value = []
    amount.value = 0
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}
void refresh()

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
    <div v-loading="loading" class="log-list">
      <ZLogCard :log="log" v-for="log in logs" :key="log._id" />
    </div>
    <div class="py-2">
      <ZPagination
        v-model:current-page="page"
        v-model:page-size="perpage"
        :total="amount"
      />
    </div>
  </div>
</template>

<style scoped>
/* 遮罩只覆盖条目区，上方的筛选输入与底部分页保持可读可点 */
.log-list {
  position: relative;
}
</style>
