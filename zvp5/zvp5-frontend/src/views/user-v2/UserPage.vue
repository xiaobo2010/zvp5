<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Segmented from '@/ui/ux/Segmented.vue'
import {
  ElLoading,
  ElButton,
  ElMessage,
  ElMessageBox,
  ElNotification,
  ElDialog,
  ElInput,
  ElAlert
} from 'element-plus'
import { ZActivityList } from '@/components'
import type { User } from '@/../types'
import api from '@/api'
import {
  clearUserPrimaryDevice,
  issueTempPassword,
  type TempPasswordIssueResult
} from '@/api/user/device'
import { isTemporaryTokenCancelled, withTemporaryToken } from '@/plugins/short-token'
import { aiErrorOf } from '@/api/ai/errors'
import { useClipboard } from '@vueuse/core'
import dayjs from '@/plugins/dayjs'
import ZUserModification from '@/components/group/ZUserModification.vue'
import { Info, Log, ViewList } from '@icon-park/vue-next'
import ZLogList from '@/components/log/ZLogList.vue'
import ZSceneRunDialog from '@/components/server/ZSceneRunDialog.vue'
import { VOLUNTEER_ACTIVITY_LIMIT, buildVolunteerContextMarkdown } from '@/utils/volunteerAnalysis'

/**
 * 封禁元数据由 `GET /v3/users/{id}` 附带返回;共享的 User 类型还没声明这几个字段,
 * 这里就地扩展,避免为一个页面改动全局类型(改动面越小越好)。
 */
interface UserWithBan extends User {
  ban_reason?: string | null
  ban_permanent?: boolean
  ban_until?: string | null
  primary_xuehai_id?: string | null
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
  const loading = ElLoading.service({ fullscreen: true })
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

// 封禁时长:预设天数 + 自定义,默认「永久」= 从前「管理员封禁一律永久」的旧行为。
// 只把天数交给后端,到期时刻由服务端按本地时区算,浏览器时区不影响落库。
type BanDurationChoice = '1' | '3' | '7' | '14' | '30' | 'permanent' | 'custom'
const BAN_DURATION_PRESETS = ['1', '3', '7', '14', '30'] as const
const BAN_DURATION_MAX_DAYS = 36500

const banDuration = ref<BanDurationChoice>('permanent')
const banCustomDays = ref('')

const banDurationOptions = computed(() => [
  ...BAN_DURATION_PRESETS.map((value) => ({
    value,
    label: t('user.ban.durationDays', { n: Number(value) })
  })),
  { value: 'permanent', label: t('user.ban.durationPermanent') },
  { value: 'custom', label: t('user.ban.durationCustom') }
])

const banCustomDaysNumber = computed(() => {
  const raw = banCustomDays.value.trim()
  if (!/^\d+$/.test(raw)) return null
  const days = Number(raw)
  return days >= 1 && days <= BAN_DURATION_MAX_DAYS ? days : null
})

// 自定义天数非法(空/小数/越界)时整张表单不可提交,不让坏值落到后端换 422。
const banDurationValid = computed(
  () => banDuration.value !== 'custom' || banCustomDaysNumber.value !== null
)

const banDurationDays = computed<number | null>(() => {
  if (banDuration.value === 'permanent') return null
  if (banDuration.value === 'custom') return banCustomDaysNumber.value
  return Number(banDuration.value)
})

const banUntilPreview = computed(() => {
  if (!banDurationValid.value) return ''
  if (banDurationDays.value === null) return t('user.ban.permanentHint')
  return t('user.ban.autoUnbanAt', {
    time: dayjs().add(banDurationDays.value, 'day').format('YYYY-MM-DD HH:mm')
  })
})

const banReasonTrimmed = computed(() => banReason.value.trim())
const banReasonValid = computed(() => {
  const length = banReasonTrimmed.value.length
  return length >= 1 && length <= 200
})

const banReasonText = computed(
  () =>
    (user.value as UserWithBan | undefined)?.ban_reason ||
    t('manage.personalPanel.banReasonFallback')
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

// 封禁标签上的期限:限时封禁显示「至 YYYY-MM-DD HH:mm」,永久就写「永久」,
// 让管理员在页头一眼看出这号会不会自己解封。
const banPeriodText = computed(() => {
  const banned = user.value as UserWithBan | undefined
  if (!banned?.banned) return ''
  if (banned.ban_permanent) return t('user.ban.periodPermanent')
  if (!banned.ban_until) return ''
  const expiresAt = dayjs(banned.ban_until)
  if (!expiresAt.isValid()) return ''
  return t('user.ban.periodUntil', { time: expiresAt.format('YYYY-MM-DD HH:mm') })
})

const banTagText = computed(() => {
  const tag = t('manage.personalPanel.bannedTag')
  return banPeriodText.value ? `${tag} · ${banPeriodText.value}` : tag
})

function openBanDialog() {
  banReason.value = ''
  banReasonTouched.value = false
  banDuration.value = 'permanent'
  banCustomDays.value = ''
  banDialogVisible.value = true
}

async function submitBan() {
  banReasonTouched.value = true
  if (!user.value || banSubmitting.value || !banReasonValid.value || !banDurationValid.value) return
  banSubmitting.value = true
  try {
    const result = await api.user.updateBanned(
      id.value,
      true,
      banReasonTrimmed.value,
      banDurationDays.value
    )
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
    await ElMessageBox.confirm(t('manage.personalPanel.unbanConfirm', { name: user.value.name }), {
      type: 'warning'
    })
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
      t('activity.member.userDelete.title'),
      {
        type: 'warning',
        confirmButtonText: t('activity.member.userDelete.confirm'),
        cancelButtonText: t('activity.member.userDelete.cancel'),
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  let result: boolean
  try {
    result = await withTemporaryToken(
      String(userStore._id),
      t('manage.personalPanel.deleteVerifyReason'),
      (token) => api.user.deleteUser(id.value, token)
    )
  } catch (error) {
    if (isTemporaryTokenCancelled(error)) return
    ElMessage({
      message: describeDeviceError(error, 'manage.personalPanel.deleteFailed'),
      type: 'error',
      grouping: true,
      plain: true
    })
    return
  }
  if (result) {
    ElNotification({
      title: t('manage.personalPanel.deletedNotification', { name: user.value.name }),
      type: 'success'
    })
    // 用户已不存在，留在详情页只会拿到 404：返回来源页（列表会在挂载时重新拉取）
    router.back()
  }
}

// 签发临时密码的服务端权限：admin/department 全校、secretary 限同班(同班判定
// 只有服务端拿得到学年班级关系，前端只做入口可见性，403 时原样展示错误)。
const canIssueTempPassword = computed(
  () =>
    userStore.position.includes('admin') ||
    userStore.position.includes('department') ||
    userStore.position.includes('secretary')
)

const tempPasswordDialogVisible = ref(false)
const tempPasswordIssued = ref<TempPasswordIssueResult | null>(null)
const tempPasswordIssuing = ref(false)
const { copy: copyToClipboard, copied: tempPasswordCopied } = useClipboard({
  copiedDuring: 2000
})

const tempPasswordExpiresText = computed(() => {
  const raw = tempPasswordIssued.value?.expires_at
  if (!raw) return ''
  const expiresAt = dayjs(raw)
  return expiresAt.isValid() ? expiresAt.format('YYYY-MM-DD HH:mm') : ''
})

// 主设备绑定：只在已绑定时展示（存量用户为空，不占版面）。
const primaryDevice = computed(() => {
  const value = (user.value as UserWithBan | undefined)?.primary_xuehai_id
  return value ? String(value) : ''
})

// 签发/清绑走 aiRequest：错误体可能是字符串 detail（权限不足），此时
// AiRequestError 的 message 为空、code 就是整句中文，展示两者兜底。
function describeDeviceError(error: unknown, fallbackKey: string): string {
  const aiError = aiErrorOf(error)
  return aiError?.message || aiError?.code || t(fallbackKey)
}

async function issueTempPasswordForUser() {
  if (!user.value || tempPasswordIssuing.value) return
  try {
    await ElMessageBox.confirm(
      t('manage.personalPanel.tempPassword.confirm', { name: user.value.name }),
      t('manage.personalPanel.tempPassword.confirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('manage.personalPanel.tempPassword.confirmButton'),
        cancelButtonText: t('action.cancel', '取消')
      }
    )
  } catch {
    return
  }
  tempPasswordIssuing.value = true
  try {
    tempPasswordIssued.value = await withTemporaryToken(
      String(userStore._id),
      t('manage.personalPanel.tempPassword.verifyReason'),
      (token) => issueTempPassword(id.value, token)
    )
    tempPasswordDialogVisible.value = true
  } catch (error) {
    if (isTemporaryTokenCancelled(error)) return
    ElMessage({
      message: describeDeviceError(error, 'manage.personalPanel.tempPassword.failed'),
      type: 'error',
      grouping: true,
      plain: true
    })
  } finally {
    tempPasswordIssuing.value = false
  }
}

async function copyTempPassword() {
  const code = tempPasswordIssued.value?.code
  if (!code) return
  await copyToClipboard(code).catch(() => {})
}

// 明文只该留在这一次弹窗里：关闭后立即从组件态抹掉，避免它跟着页面驻留。
function forgetTempPassword() {
  tempPasswordIssued.value = null
}

async function clearPrimaryDevice() {
  if (!user.value) return
  try {
    await ElMessageBox.confirm(
      t('manage.personalPanel.clearDevice.confirm', { name: user.value.name }),
      t('manage.personalPanel.clearDevice.title'),
      {
        type: 'warning',
        confirmButtonText: t('manage.personalPanel.clearDevice.confirmButton'),
        cancelButtonText: t('action.cancel', '取消'),
        confirmButtonClass: 'el-button--danger'
      }
    )
  } catch {
    return
  }
  try {
    await withTemporaryToken(
      String(userStore._id),
      t('manage.personalPanel.clearDevice.verifyReason'),
      (token) => clearUserPrimaryDevice(id.value, token)
    )
    ElNotification({
      title: t('manage.personalPanel.clearDevice.done', { name: user.value.name }),
      type: 'success'
    })
    await getUser()
  } catch (error) {
    if (isTemporaryTokenCancelled(error)) return
    ElMessage({
      message: describeDeviceError(error, 'manage.personalPanel.clearDevice.failed'),
      type: 'error',
      grouping: true,
      plain: true
    })
  }
}

// ---- 义工智能分析 ----
// volunteer 场景是只读的：结果只展示在弹窗里，没有可写回的对象，因此不监听
// ZSceneRunDialog 的 apply，弹窗会自动隐藏「写回」按钮。
const analysisVisible = ref(false)
const analysisContext = ref('')
const analysisLoading = ref(false)

// 工时与活动都是「锦上添花」的参考：任一失败只降级成占位文案 + 一次轻提示，
// 不能挡住弹窗（身份信息本身已在页面上）。
async function buildVolunteerContext(): Promise<string> {
  const [timeResult, activitiesResult] = await Promise.allSettled([
    api.user.time.read(id.value),
    api.user.activity.read(id.value, 1, VOLUNTEER_ACTIVITY_LIMIT)
  ])

  const time = timeResult.status === 'fulfilled' ? timeResult.value : null
  const activities =
    activitiesResult.status === 'fulfilled' ? (activitiesResult.value?.activities ?? []) : null

  if (timeResult.status === 'rejected' || activitiesResult.status === 'rejected') {
    ElMessage({
      message: t('user.volunteerAnalysis.partialFailed'),
      type: 'warning',
      grouping: true,
      plain: true
    })
  }

  return buildVolunteerContextMarkdown({ user: user.value, time, activities })
}

async function openVolunteerAnalysis() {
  if (analysisLoading.value) return
  analysisLoading.value = true
  try {
    analysisContext.value = await buildVolunteerContext()
    analysisVisible.value = true
  } finally {
    analysisLoading.value = false
  }
}

getUser().then(() => {
  if (
    !(
      userStore.position.includes('admin') ||
      userStore.position.includes('department') ||
      (userStore.position.includes('secretary') && user.value?.group[0] === userStore.class_id)
    )
  ) {
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
  () => {
    const curPage = route.params.action?.toString()
    current.value = curPage && curPage !== '' ? curPage : 'info'
  }
)
</script>

<template>
  <div v-if="id" class="yg2-page user-page">
    <button class="user-back" type="button" @click="router.back()">
      <span aria-hidden="true">‹</span> 返回
    </button>

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

.user-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 12px 0 8px;
  border: 0;
  border-radius: var(--yg2-r-pill);
  background: transparent;
  color: var(--yg2-text-secondary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.user-back:hover {
  background: var(--yg2-surface-sunken);
  color: var(--yg2-text);
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
</style>
