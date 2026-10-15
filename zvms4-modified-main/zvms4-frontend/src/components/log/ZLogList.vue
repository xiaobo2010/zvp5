<script setup lang="ts">
import type { Log } from '@/../types/log'
import { ref, watch, toRefs } from 'vue'
import api from '@/api'
import ZLogCard from '@/components/log/ZLogCard.vue'
import { ElPagination, ElInput } from 'element-plus'

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
    <div class="flex gap-2">
      <ElInput v-model.lazy="query" @keydown.enter="query2 = query" @blur="query2 = query"></ElInput>
      <ElInput
        v-model.lazy="xuehai_id"
        class="w-40 shrink-0"
        placeholder="学海 ID"
        @keydown.enter="xuehai_id2 = xuehai_id"
        @blur="xuehai_id2 = xuehai_id"
      ></ElInput>
    </div>
    <ZLogCard v-loading="loading" :log="log" v-for="log in logs" :key="log._id" />
    <div class="py-2">
      <ElPagination
        v-model:current-page="page"
        v-model:page-size="perpage"
        :pager-count="3"
        :total="amount"
        layout="total, prev, pager, next, sizes, jumper"
        background
        :page-sizes="[3, 5, 8, 10, 15, 20]"
      />
    </div>
  </div>
</template>

<style scoped></style>
