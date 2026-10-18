<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElLoading, ElIcon, ElButton, ElTag, ElMessageBox, ElNotification, ElTooltip } from 'element-plus'
import { ZActivityList } from '@/components'
import { ArrowLeft, Edit } from '@element-plus/icons-vue'
import type { User } from '@/../types'
import api from '@/api'
import ZUserModification from '@/components/group/ZUserModification.vue'
import { Info, Log, ViewList } from '@icon-park/vue-next'
import ZLogList from '@/components/log/ZLogList.vue'

interface SegmentedTab {
  label: string
  value: string
  icon: Component
  display?: boolean
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
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
  const result = await api.user.readOne_v3(id.value).catch(() => {
    loading.close()
  })
  if (result) {
    user.value = result
  }
  loading.close()
}

const isSelf = computed(() => !!userStore._id && userStore._id === id.value)
const banSelfBlocked = computed(() => isSelf.value && !user.value?.banned)

async function toggleBanned() {
  if (!user.value) return
  if (banSelfBlocked.value) return
  const isBanned = user.value.banned
  try {
    await ElMessageBox.confirm(
      t(isBanned ? 'manage.personalPanel.unbanConfirm' : 'manage.personalPanel.banConfirm', { name: user.value.name }),
      { type: 'warning' }
    )
  } catch {
    return
  }
  const result = await api.user.updateBanned(id.value, !isBanned)
  if (result) {
    ElNotification({
      title: t(isBanned ? 'manage.personalPanel.unbannedNotification' : 'manage.personalPanel.bannedNotification', { name: user.value.name }),
      type: 'success'
    })
    await getUser()
  }
}

async function deleteUser() {
  if (!user.value) return
  if (isSelf.value) return
  try {
    await ElMessageBox.confirm(
      t('manage.personalPanel.deleteConfirm', { name: user.value.name }),
      { type: 'warning' }
    )
  } catch {
    return
  }
  const result = await api.user.deleteUser(id.value)
  if (result) {
    ElNotification({
      title: t('manage.personalPanel.deletedNotification', { name: user.value.name }),
      type: 'success'
    })
    // 用户已不存在，留在详情页只会拿到 404：返回来源页（列表会在挂载时重新拉取）
    router.back()
  }
}

getUser().then(() => {
  if (!(userStore.position.includes('admin') || userStore.position.includes('department') || userStore.position.includes('secretary') && user.value?.group[0] === userStore.class_id)) {
    router.replace('/not-found')
  }
})

const curPage = route.params.action?.toString()

const current = ref(curPage && curPage !== '' ? curPage : 'info')

const tabs = ref<SegmentedTab[]>(
  [
    {
      label: 'Info',
      value: 'info',
      icon: Info
    },
    {
      label: 'Activity',
      value: 'activity',
      icon: ViewList
    },
    {
      label: 'Modify',
      value: 'modify',
      icon: Edit,
      display: userStore.position.includes('admin')
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
        <ElTag v-if="user?.banned" type="danger" class="ml-2">
          {{ t('manage.personalPanel.bannedTag') }}
        </ElTag>
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs">
          <template #default="props">
            <div class="flex flex-col items-center gap-2 p-2">
              <ElIcon :size="18" class="mt-2">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              {{ t(('manage.personalPanel.tabs.' + (props.item as SegmentedTab).value) as string) }}
            </div>
          </template>
        </ElSegmented>
        <ElTooltip
          v-if="userStore.position.includes('admin') && user"
          :content="t('manage.personalPanel.banSelfForbidden')"
          :disabled="!banSelfBlocked"
          placement="bottom"
        >
          <span class="ml-4 inline-flex">
            <ElButton type="danger" text bg :disabled="banSelfBlocked" @click="toggleBanned">
              {{ user.banned ? t('manage.personalPanel.unban') : t('manage.personalPanel.ban') }}
            </ElButton>
          </span>
        </ElTooltip>
        <ElTooltip
          v-if="userStore.position.includes('admin') && user"
          :content="t('manage.personalPanel.deleteSelfForbidden')"
          :disabled="!isSelf"
          placement="bottom"
        >
          <span class="ml-2 inline-flex">
            <ElButton type="danger" text bg :disabled="isSelf" @click="deleteUser">
              {{ t('manage.groupDetails.userList.columns.remove') }}
            </ElButton>
          </span>
        </ElTooltip>
      </template>
    </ElPageHeader>
    <div v-if="user && current === 'info'">
      <ZActivityMember :id="id" mode="card" :person="user ?? undefined" />
    </div>
    <div v-else-if="user && current === 'activity'">
      <ZActivityList :perspective="id" role="mine" :key="id" />
    </div>
    <div v-else-if="user && current === 'modify'">
      <ZUserModification :id="id" mode="modify" cid="" />
    </div>
    <div v-else-if="user && current === 'logs'">
      <ZLogList :user="id" />
    </div>
  </div>
</template>
