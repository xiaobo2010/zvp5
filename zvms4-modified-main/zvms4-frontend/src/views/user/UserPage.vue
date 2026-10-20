<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader, ElSegmented, ElLoading, ElIcon, ElButton, ElTag, ElMessageBox, ElNotification, ElTooltip, ElDialog, ElInput, ElAlert } from 'element-plus'
import { ZActivityList } from '@/components'
import { ArrowLeft, Edit } from '@element-plus/icons-vue'
import type { User } from '@/../types'
import api from '@/api'
import dayjs from '@/plugins/dayjs'
import ZUserModification from '@/components/group/ZUserModification.vue'
import { Info, Log, ViewList } from '@icon-park/vue-next'
import ZLogList from '@/components/log/ZLogList.vue'

/**
 * 封禁元数据由 `GET /v3/users/{id}` 附带返回;共享的 User 类型还没声明这几个字段,
 * 这里就地扩展,避免为一个页面改动全局类型(改动面越小越好)。
 */
interface UserWithBan extends User {
  ban_reason?: string | null
  ban_permanent?: boolean
  ban_until?: string | null
}

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

// 封禁弹窗:管理员封禁是永久动作且需要写原因,用页面内小弹窗替代 ElMessageBox.confirm,
// 后者只能给一段文字,放不下必填的 1-200 字原因输入。
const banDialogVisible = ref(false)
const banReason = ref('')
const banReasonTouched = ref(false)
const banSubmitting = ref(false)

const banReasonTrimmed = computed(() => banReason.value.trim())
const banReasonValid = computed(() => {
  const length = banReasonTrimmed.value.length
  return length >= 1 && length <= 200
})

const banReasonText = computed(
  () => (user.value as UserWithBan | undefined)?.ban_reason || t('manage.personalPanel.banReasonFallback')
)

// 只有真被封禁才需要这行;到期时间按本地时区展示,永久封禁没有 ban_until。
const banUntilText = computed(() => {
  const banned = user.value as UserWithBan | undefined
  if (!banned?.banned) return ''
  if (banned.ban_permanent) return t('manage.personalPanel.banPermanent')
  if (!banned.ban_until) return ''
  const expiresAt = dayjs(banned.ban_until)
  if (!expiresAt.isValid()) return ''
  return expiresAt.format('YYYY-MM-DD HH:mm')
})

function openBanDialog() {
  banReason.value = ''
  banReasonTouched.value = false
  banDialogVisible.value = true
}

async function submitBan() {
  banReasonTouched.value = true
  if (!user.value || banSubmitting.value || !banReasonValid.value) return
  banSubmitting.value = true
  try {
    const result = await api.user.updateBanned(id.value, true, banReasonTrimmed.value)
    if (result) {
      banDialogVisible.value = false
      ElNotification({
        title: t('manage.personalPanel.bannedNotification', { name: user.value.name }),
        type: 'success'
      })
      await getUser()
    }
  } finally {
    banSubmitting.value = false
  }
}

async function toggleBanned() {
  if (!user.value) return
  if (banSelfBlocked.value) return
  const isBanned = user.value.banned
  // 解封无需原因,保持原来的确认框;封禁改走弹窗收原因。
  if (!isBanned) {
    openBanDialog()
    return
  }
  try {
    await ElMessageBox.confirm(
      t('manage.personalPanel.unbanConfirm', { name: user.value.name }),
      { type: 'warning' }
    )
  } catch {
    return
  }
  const result = await api.user.updateBanned(id.value, false)
  if (result) {
    ElNotification({
      title: t('manage.personalPanel.unbannedNotification', { name: user.value.name }),
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
  <div class="px-6 py-4">
    <ElPageHeader v-if="id" :icon="ArrowLeft" @back="router.back()" class="py-2">
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
    <ElAlert
      v-if="user?.banned"
      type="error"
      :closable="false"
      :title="t('manage.personalPanel.banReasonLabel') + '：' + banReasonText"
      class="mb-3"
    >
      <template #default>
        <span v-if="banUntilText">{{ t('manage.personalPanel.banUntil', { time: banUntilText }) }}</span>
      </template>
    </ElAlert>
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
    <ElDialog
      v-model="banDialogVisible"
      :title="t('manage.personalPanel.banDialogTitle')"
      width="min(440px, 92vw)"
      align-center
      append-to-body
    >
      <p class="text-sm mb-3">{{ t('manage.personalPanel.banDialogNotice') }}</p>
      <label class="block text-sm mb-1">{{ t('manage.personalPanel.banReasonLabel') }}</label>
      <ElInput
        v-model="banReason"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        :placeholder="t('manage.personalPanel.banReasonPlaceholder')"
        @blur="banReasonTouched = true"
      />
      <p v-if="banReasonTouched && !banReasonValid" class="text-xs text-red-500 mt-1">
        {{ t('manage.personalPanel.banReasonInvalid') }}
      </p>
      <template #footer>
        <ElButton @click="banDialogVisible = false">
          {{ t('action.cancel', '取消') }}
        </ElButton>
        <ElButton type="danger" :disabled="!banReasonValid" :loading="banSubmitting" @click="submitBan">
          {{ t('manage.personalPanel.banDialogConfirm') }}
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>
