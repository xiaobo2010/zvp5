<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { ref, watch, reactive, computed } from 'vue'
import {
  ElLoading,
  ElPageHeader,
  ElSegmented,
  ElIcon,
  ElDivider,
  ElRow,
  ElCol,
  ElProgress,
  ElSwitch
} from 'element-plus'
import { ArrowLeft, Edit } from '@element-plus/icons-vue'
import type { User } from '@/types'
import { Info, Log, ViewList } from '@icon-park/vue-next'
import ZLogList from '@/components/log/ZLogList.vue'

import 'element-plus/theme-chalk/el-segmented.css'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const state = useState()
const id = ref<string>(route.params.id as string)
const user = ref<User>()

const time = reactive({
  socialPractice: NaN as number,
  onCampus: NaN as number,
  offCampus: NaN as number,
  loaded: false,
})

const convert = ref(state.convert)

const colors = [
  { color: 'var(--el-color-danger)', percentage: 25 },
  { color: 'var(--el-color-primary)', percentage: 25.01 },
  { color: 'var(--el-color-primary)', percentage: 99.99 },
  { color: 'var(--el-color-success)', percentage: 100 },
]

const clamp = (x: number) => Math.min(x, 100)
const doConvert = (raw: number, other: number, threshold: number, rate: number) => raw + Number((Math.max(other - threshold, 0) / rate).toFixed(1))

watch(convert, () => state.set('convert', convert.value))

const onCampus = computed(() => convert.value ? doConvert(time.onCampus, time.offCampus, 15, 2) : time.onCampus)
const offCampus = computed(() => convert.value ? doConvert(time.offCampus, time.onCampus, 25, 3) : time.offCampus)

async function getTime() {
  time.loaded = false
  try {
    const result = await (await import('@/api')).user.time.read(id.value)
    if (result) {
      time.offCampus = result['off-campus']
      time.onCampus = result['on-campus']
      time.socialPractice = result['social-practice']
    }
  } catch (_) {
    // 无权限或其它错误：时长保持 NaN，静默处理
  } finally {
    time.loaded = true
  }
}

watch(
  () => route.params.id,
  (value) => {
    id.value = value as string
    getUser()
    getTime()
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

getTime()

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
      <ElDivider content-position="left">
        <span class="section-title">义工时间</span>
      </ElDivider>
      <div class="time-panel" :data-loaded="String(time.loaded)" v-loading="!time.loaded">
        <ElRow>
          <ElCol class="time-slot" :span="6" :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
            <ElProgress type="dashboard" :percentage="clamp(onCampus / 25 * 100)" :color="colors">
              <p class="text-2xl time">{{ onCampus }}h</p>
              <p class="text-sm">校内义工</p>
            </ElProgress>
          </ElCol>
          <ElCol class="time-slot" :span="6" :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
            <ElProgress type="dashboard" :percentage="clamp(offCampus / 15 * 100)" :color="colors">
              <p class="text-2xl time">{{ offCampus }}h</p>
              <p class="text-sm">校外义工</p>
            </ElProgress>
          </ElCol>
          <ElCol class="hidden-sm-and-down time-slot" :span="6" :xs="0" :sm="0" :md="6" :lg="6" :xl="6">
            <ElProgress type="dashboard" :percentage="clamp(time.socialPractice / 18 * 100)" :color="colors">
              <p class="text-2xl time">{{ time.socialPractice }}h</p>
              <p class="text-sm">社会实践</p>
            </ElProgress>
          </ElCol>
          <ElCol class="hidden-sm-and-down conv-slot" :span="6" :xs="0" :sm="0" :md="6" :lg="6" :xl="6">
            <span>显示折算</span>
            <ElSwitch v-model="convert"></ElSwitch>
          </ElCol>
        </ElRow>
        <ElRow class="hidden-md-and-up">
          <ElCol class="time-slot" :span="12">
            <ElProgress type="dashboard" :percentage="clamp(time.socialPractice / 18 * 100)" :color="colors">
              <p class="text-2xl time">{{ time.socialPractice }}h</p>
              <p class="text-sm">社会实践</p>
            </ElProgress>
          </ElCol>
          <ElCol class="conv-slot" :span="12">
            <span>显示折算</span>
            <ElSwitch v-model="convert"></ElSwitch>
          </ElCol>
        </ElRow>
      </div>
      <ZActivityMember :id="id" mode="card" />
    </div>
    <div v-else-if="user && current === 'logs'">
      <ZLogList :user="id" />
    </div>
  </div>
</template>

<style scoped>
.section-title {
    font-size: large;
    font-weight: bold;
}

.time-panel {
    display: flex;
    flex-direction: column;
    gap: 2rem;
    margin-top: 1.5rem;
    margin-bottom: 1.5rem;
}

.time-slot, .conv-slot {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: .5rem;
}

.time-panel .time {
    transition: opacity .5s ease-in-out;
}

.time-panel:not([data-loaded="true"]) .time {
    opacity: 0;
}
</style>
