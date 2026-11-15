<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'
import { ElLoading, ElButton, ElMessageBox, ElNotification, ElDialog, ElInput, ElAlert } from 'element-plus'
import { ZActivityList } from '@/components'
import type { User } from '@/../types'
import api from '@/api'
import dayjs from '@/plugins/dayjs'
import ZUserModification from '@/components/group/ZUserModification.vue'
import { Info, Log, ViewList } from '@icon-park/vue-next'
import ZLogList from '@/components/log/ZLogList.vue'
import GlassBackButton from '@/ui/glass/GlassBackButton.vue'

/**
 * 封禁元数据由 `GET /v1/users/{id}` 附带返回;共享的 User 类型还没声明这几个字段,
 * 这里就地扩展,避免为一个页面改动全局类型(改动面越小越好)。
 */
interface UserWithBan extends User {
  ban_reason?: string | null
  ban_permanent?: boolean
  ban_until?: string | null
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
const isAdmin = computed(() => userStore.position.includes('admin'))
const initials = computed(() => (user.value?.name || '').slice(-2) || '?')
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

const segmentItems = computed(() =>
  tabs.value
    .filter((tab) => tab.display !== false)
    .map((tab) => ({
      value: tab.value,
      label: t(('manage.personalPanel.tabs.' + tab.value) as string)
    }))
)

const tabs = ref<{ value: string; display?: boolean }[]>(
  [
    { value: 'info' },
    { value: 'activity' },
    { value: 'modify', display: userStore.position.includes('admin') },
    { value: 'logs', display: userStore.position.includes('admin') }
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
  <div v-if="id" class="yg2-page user-page">
    <GlassBackButton class="user-back" @click="router.back()" />

    <header class="yg2-page-header user-header">
      <div class="user-id">
        <div class="user-avatar">{{ initials }}</div>
        <div>
          <h1 class="yg2-page-header__title user-name">
            {{ user?.name || '…' }}
            <span v-if="user?.banned" class="yg2-badge yg2-badge--danger">
              {{ t('manage.personalPanel.bannedTag') }}
            </span>
          </h1>
          <p class="yg2-page-header__subtitle">
            {{ user?.id }} · {{ user?.class_id || t('profile.noClass') }}
          </p>
        </div>
      </div>
      <div class="yg2-page-header__actions">
        <button
          v-if="isAdmin && user"
          class="yg2-btn yg2-btn--danger yg2-btn--sm"
          type="button"
          :disabled="banSelfBlocked"
          :title="banSelfBlocked ? t('manage.personalPanel.banSelfForbidden') : ''"
          @click="toggleBanned"
        >
          {{ user.banned ? t('manage.personalPanel.unban') : t('manage.personalPanel.ban') }}
        </button>
        <button
          v-if="isAdmin && user"
          class="yg2-btn yg2-btn--secondary yg2-btn--sm"
          type="button"
          :disabled="isSelf"
          :title="isSelf ? t('manage.personalPanel.deleteSelfForbidden') : ''"
          @click="deleteUser"
        >
          {{ t('manage.groupDetails.userList.columns.remove') }}
        </button>
      </div>
    </header>

    <Segmented
      v-model="current"
      class="user-tabs"
      :items="segmentItems"
      aria-label="个人面板"
    />
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
      <ZActivityList :perspective="id" role="mine" :key="id" embed />
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

<style scoped>
.user-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}



.user-id {
  display: flex;
  align-items: center;
  gap: 16px;
}

.user-avatar {
  display: grid;
  place-items: center;
  width: 64px;
  height: 64px;
  border-radius: var(--yg2-r-pill);
  background: linear-gradient(160deg, rgba(0, 113, 227, 0.16), rgba(0, 113, 227, 0.04));
  color: var(--yg2-accent);
  font-size: 20px;
  font-weight: 600;
}

.user-name {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-tabs {
  align-self: flex-start;
}

.user-back {
  align-self: flex-start;
}
</style>
