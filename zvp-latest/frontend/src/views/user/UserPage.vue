<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores'
import { ref, watch } from 'vue'
import { ElLoading, ElPageHeader, ElSegmented, ElIcon } from 'element-plus'
import { ArrowLeft, Edit } from '@element-plus/icons-vue'
import type { User } from '@/types'
import { Info, Log, ViewList } from '@icon-park/vue-next'
import ZLogList from '@/components/log/ZLogList.vue'

import 'element-plus/theme-chalk/el-segmented.css'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const id = ref<string>(route.params.id as string)
const user = ref<User>()

watch(
  () => route.params.id,
  (value) => {
    id.value = value as string
    getUser()
  }
)

async function getUser() {
  const loading = ElLoading.service({ fullscreen: true})
  const result = await (await import('@/api')).user.readOne(id.value).catch(() => {
    loading.close()
  })
  if (result) {
    user.value = result
  }
  loading.close()
}

getUser().then(() => {
  if (!(userStore.position.includes('admin') || userStore.position.includes('department') || userStore.position.includes('secretary') && user.value?.group[0] === userStore.class_id)) {
    router.replace('/not-found')
  }
})

const curPage = route.params.action?.toString()

const current = ref(curPage && curPage !== '' ? curPage : 'info')

const tabs = ref(
  [
    {
      label: 'Info',
      value: 'info',
      icon: Info
    },
    {
      label: 'Logs',
      value: 'logs',
      icon: Log,
      display: userStore.position.includes('admin')
    }
  ].filter((x) => x.display ?? true)
)

watch(current, () => {
  router.replace('/user/' + id.value + '/' + current.value)
})

watch(
  () => route.params.action,
  (value) => {
    console.log(value)
    const curPage = route.params.action?.toString()
    current.value = curPage && curPage !== '' ? curPage : 'info'
  }
)
</script>

<template>
  <div class="px-16 py-8">
    <ElPageHeader v-if="id" :icon="ArrowLeft" @back="router.back()" class="py-4">
      <template #content>
        {{ user?.name }}
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="flex flex-row items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="props.item.icon" />
              </ElIcon>
              {{ props.item.label }}
            </div>
          </template>
        </ElSegmented>
      </template>
    </ElPageHeader>
    <div v-if="user && current === 'info'">
      <ZActivityMember :id="id" mode="card" />
    </div>
    <div v-else-if="user && current === 'logs'">
      <ZLogList :user="id" />
    </div>
  </div>
</template>
