<script setup lang="ts">
import ZActivityMember from '@/components/activity/ZActivityMember.vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user.ts'
import { computed, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElPageHeader } from 'element-plus/es/components/page-header/index.mjs'
import { ElSegmented } from 'element-plus/es/components/segmented/index.mjs'
import { ElLoading } from 'element-plus/es/components/loading/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElTag } from 'element-plus/es/components/tag/index.mjs'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElNotification } from 'element-plus/es/components/notification/index.mjs'
import { ElTooltip } from 'element-plus/es/components/tooltip/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import { ElSelect } from 'element-plus/es/components/select/index.mjs'
import { ElOption } from 'element-plus/es/components/select/index.mjs'
import { ElAlert } from 'element-plus/es/components/alert/index.mjs'
import { ZActivityList } from '@/components'
import { ArrowLeft, Edit } from '@element-plus/icons-vue'
import type { User } from '@/../types'
import api from '@/api'
import { issueTempPassword, type TempPasswordIssueResult } from '@/api/user/device'
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
 * 封禁元数据由 `GET /v1/users/{id}` 附带返回;共享的 User 类型还没声明这几个字段,
 * 这里就地扩展,避免为一个页面改动全局类型(改动面越小越好)。
 */
interface UserWithBan extends User {
  ban_reason?: string | null
  ban_permanent?: boolean
  ban_until?: string | null
  primary_xuehai_id?: string | null
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

const isAdmin = computed(() => userStore.position.includes('admin'))
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
  () => {
    const curPage = route.params.action?.toString()
    current.value = curPage && curPage !== '' ? curPage : 'info'
  }
)
</script>

<template>
  <div class="px-6 py-4 yg-v2-flow yg-user-page phx phx-user">
    <ElPageHeader v-if="id" :icon="ArrowLeft" @back="router.back()" class="phx-page-head">
      <template #content>
        <p class="phx-head__eyebrow">User · ZVMS</p>
        <div class="phx-head__title-row">
          <h1 class="phx-head__title">{{ user?.name }}</h1>
          <ElTag v-if="user?.banned" type="danger" class="phx-ban-tag">
            {{ banTagText }}
          </ElTag>
        </div>
        <p class="phx-head__lead">查看用户资料、活动记录与账号状态。</p>
        <span class="phx-head__index" aria-hidden="true">05</span>
      </template>
      <template #extra>
        <ElSegmented v-model="current" :options="tabs" class="phx-seg">
          <template #default="props">
            <div class="phx-tab-inner">
              <ElIcon :size="16" class="phx-tab-icon">
                <Component :is="(props.item as SegmentedTab).icon" />
              </ElIcon>
              <span class="phx-tab-label">
                {{
                  t(('manage.personalPanel.tabs.' + (props.item as SegmentedTab).value) as string)
                }}
              </span>
            </div>
          </template>
        </ElSegmented>
        <div class="phx-actions">
          <span v-if="isAdmin && user" class="phx-action">
            <ElButton text bg :loading="analysisLoading" @click="openVolunteerAnalysis">
              {{ t('user.volunteerAnalysis.action') }}
            </ElButton>
          </span>
          <span v-if="canIssueTempPassword && user" class="phx-action">
            <ElButton text bg :loading="tempPasswordIssuing" @click="issueTempPasswordForUser">
              {{ t('manage.personalPanel.tempPassword.action') }}
            </ElButton>
          </span>
          <ElTooltip
            v-if="userStore.position.includes('admin') && user"
            :content="t('manage.personalPanel.banSelfForbidden')"
            :disabled="!banSelfBlocked"
            placement="bottom"
          >
            <span class="phx-action">
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
            <span class="phx-action">
              <ElButton type="danger" text bg :disabled="isSelf" @click="deleteUser">
                {{ t('manage.groupDetails.userList.columns.remove') }}
              </ElButton>
            </span>
          </ElTooltip>
        </div>
      </template>
    </ElPageHeader>
    <ElAlert
      v-if="user?.banned"
      type="error"
      :closable="false"
      :title="t('manage.personalPanel.banReasonLabel') + '：' + banReasonText"
      class="phx-alert"
    >
      <template #default>
        <span v-if="banUntilText">{{
          t('manage.personalPanel.banUntil', { time: banUntilText })
        }}</span>
      </template>
    </ElAlert>
    <div v-if="user && current === 'info'" class="phx-panel phx-member" style="--phx-i: 1">
      <p v-if="primaryDevice" class="phx-primary-device">
        {{ t('manage.personalPanel.primaryDevice', { id: primaryDevice }) }}
      </p>
      <ZActivityMember :id="id" mode="card" :person="user ?? undefined" />
    </div>
    <div v-else-if="user && current === 'activity'" class="phx-activity-pane">
      <ZActivityList class="phx-activity" :perspective="id" role="mine" :key="id" />
    </div>
    <div v-else-if="user && current === 'modify'" class="phx-panel phx-content" style="--phx-i: 1">
      <ZUserModification :id="id" mode="modify" cid="" />
    </div>
    <div v-else-if="user && current === 'logs'" class="phx-panel phx-content" style="--phx-i: 1">
      <ZLogList :user="id" />
    </div>
    <ElDialog
      v-model="banDialogVisible"
      :title="t('manage.personalPanel.banDialogTitle')"
      width="min(440px, 92vw)"
      align-center
      append-to-body
    >
      <p class="text-sm mb-3">{{ t('user.ban.dialogNotice') }}</p>
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
      <label class="block text-sm mb-1 mt-3">{{ t('user.ban.durationLabel') }}</label>
      <ElSelect v-model="banDuration" class="w-full">
        <ElOption
          v-for="option in banDurationOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </ElSelect>
      <div v-if="banDuration === 'custom'" class="mt-2 flex items-center gap-2">
        <ElInput v-model="banCustomDays" inputmode="numeric" class="flex-1" />
        <span class="text-sm">{{ t('user.ban.customUnit') }}</span>
      </div>
      <p
        v-if="banDuration === 'custom' && banCustomDays.trim() && !banDurationValid"
        class="text-xs text-red-500 mt-1"
      >
        {{ t('user.ban.customInvalid') }}
      </p>
      <p v-else-if="banUntilPreview" class="text-xs text-gray-500 mt-1">
        {{ banUntilPreview }}
      </p>
      <template #footer>
        <ElButton @click="banDialogVisible = false">
          {{ t('action.cancel', '取消') }}
        </ElButton>
        <ElButton
          type="danger"
          :disabled="!banReasonValid || !banDurationValid"
          :loading="banSubmitting"
          @click="submitBan"
        >
          {{ t('manage.personalPanel.banDialogConfirm') }}
        </ElButton>
      </template>
    </ElDialog>
    <ElDialog
      v-model="tempPasswordDialogVisible"
      :title="t('manage.personalPanel.tempPassword.dialogTitle')"
      width="min(440px, 92vw)"
      align-center
      append-to-body
      :close-on-click-modal="false"
      @closed="forgetTempPassword"
    >
      <p class="text-sm leading-6 mb-3">{{ t('manage.personalPanel.tempPassword.notice') }}</p>
      <div class="flex items-center gap-2">
        <code class="phx-code-block">{{ tempPasswordIssued?.code }}</code>
        <ElButton
          class="flex-none"
          :type="tempPasswordCopied ? 'success' : 'primary'"
          @click="copyTempPassword"
        >
          {{
            tempPasswordCopied
              ? t('manage.personalPanel.tempPassword.copied')
              : t('manage.personalPanel.tempPassword.copy')
          }}
        </ElButton>
      </div>
      <p v-if="tempPasswordExpiresText" class="mt-3 text-sm text-gray-500">
        {{ t('manage.personalPanel.tempPassword.expiresAt', { time: tempPasswordExpiresText }) }}
      </p>
      <template #footer>
        <ElButton type="primary" @click="tempPasswordDialogVisible = false">
          {{ t('manage.personalPanel.tempPassword.acknowledge') }}
        </ElButton>
      </template>
    </ElDialog>
    <ZSceneRunDialog
      v-model="analysisVisible"
      scene="volunteer"
      :title="t('user.volunteerAnalysis.title')"
      :context="analysisContext"
    />
  </div>
</template>

<style scoped>
/* ==========================================================================
   Phi 棱角系统 · 用户主页（与 AboutView / UserActivity 同一套 scoped 写法）
   直角 + 不对称斜切、细线、角括号、offset 硬阴影；浅色只改强调色与硬阴影。
   注：本项目 compiler-sfc 3.5 会把 `:global(x) .a` 整条替换成 `x`，
   所以浅色覆盖写成 `html[data-theme='light'] .phx-user ...`。
   ========================================================================== */
.phx-user {
  --phx-accent: var(--phi-cyan, var(--yg-color-primary));
  --phx-accent-2: var(--phi-pink, var(--yg-color-danger));
  --phx-on-accent: #041311;
  --phx-ink: var(--yg-text-primary);
  --phx-muted: var(--yg-text-tertiary);
  --phx-surface: var(--yg-bg-surface);
  --phx-sunken: var(--yg-bg-surface-sunken);
  --phx-line: color-mix(in srgb, var(--phx-accent) 22%, transparent);
  --phx-line-soft: color-mix(in srgb, var(--phx-ink) 10%, transparent);
  --phx-hard: color-mix(in srgb, var(--phx-accent) 16%, #000);
  --phx-display: var(--phi-display, Bebas, Impact, sans-serif);
  --phx-hud: var(--phi-hud, Rajdhani, 'Arial Narrow', sans-serif);
  --phx-ease: cubic-bezier(0.19, 1, 0.22, 1);
  --phx-spring: cubic-bezier(0.34, 1.4, 0.64, 1);
  --yg-radius-xs: 0;
  --yg-radius-sm: 0;
  --yg-radius-md: 0;
  --yg-radius-lg: 0;
  --yg-radius-xl: 0;
  --el-border-radius-base: 0;
  --el-border-radius-small: 0;
  --el-border-radius-round: 0;
  --el-card-border-radius: 0;
  --el-tag-border-radius: 0;
  --el-button-border-radius: 0;
  --el-alert-border-radius-base: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yg-space-4);
}

html[data-theme='light'] .phx-user {
  --phx-accent: #007a70;
  --phx-accent-2: #c81e63;
  --phx-on-accent: #ffffff;
  --phx-hard: #101418;
  --el-color-primary: #007a70;
  --yg-color-primary: #007a70;
  --yg-focus-ring-color: #007a70;
}

/* ---------- 页头：HUD 编号 + 细网格 + 斜线 ---------- */
.phx-page-head {
  position: relative;
  margin: 0;
  padding: var(--yg-space-5) var(--yg-space-6) var(--yg-space-4);
  overflow: hidden;
  border-left: 3px solid var(--phx-accent);
  background:
    linear-gradient(
        to bottom left,
        transparent calc(50% - 1px),
        var(--phx-accent) 50%,
        transparent calc(50% + 1.5px)
      )
      right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(90deg, var(--phx-line-soft) 1px, transparent 1px) 0 0 / 24px 24px,
    linear-gradient(
      100deg,
      color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface)),
      var(--phx-surface) 58%
    );
  clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  animation: phx-head-in 420ms var(--phx-ease) both;
}

.phx-page-head::after {
  content: '';
  position: absolute;
  right: 16%;
  bottom: 0;
  width: 120px;
  height: 6px;
  background: repeating-linear-gradient(-55deg, var(--phx-accent) 0 2px, transparent 2px 7px);
  opacity: 0.55;
  pointer-events: none;
}

.phx-page-head :deep(.el-page-header__header) {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  line-height: normal;
}

.phx-page-head :deep(.el-page-header__left) {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  width: 100%;
  min-width: 0;
  margin-right: 0;
}

.phx-page-head :deep(.el-page-header__back) {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 36px;
  height: 36px;
  margin-top: 2px;
  border: 1px solid var(--phx-line);
  background: var(--phx-sunken);
  color: var(--phx-accent);
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 0 100%);
  transition:
    transform 260ms var(--phx-spring),
    background-color 180ms var(--phx-ease),
    color 180ms var(--phx-ease);
}

.phx-page-head :deep(.el-page-header__back:hover) {
  background: var(--phx-accent);
  color: var(--phx-on-accent);
  transform: translateX(-2px) skewX(-6deg);
}

.phx-page-head :deep(.el-page-header__icon) {
  display: flex;
  align-items: center;
  margin-right: 0;
  font-size: 18px;
}

.phx-page-head :deep(.el-page-header__title) {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.phx-page-head :deep(.el-divider--vertical) {
  display: none;
}

.phx-page-head :deep(.el-page-header__content) {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0 96px 0 var(--yg-space-3);
  color: var(--phx-ink);
  font-size: inherit;
}

.phx-head__eyebrow {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  margin: 0 0 var(--yg-space-2);
  color: var(--phx-accent);
  font: 400 15px / 1 var(--phx-display);
  letter-spacing: 0.22em;
  text-transform: uppercase;
  animation: phx-slide-in 420ms 40ms var(--phx-ease) both;
}

.phx-head__eyebrow::before {
  content: '';
  flex: 0 0 auto;
  width: 18px;
  height: 2px;
  background: currentColor;
  transform: skewX(-40deg);
}

.phx-head__title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  min-width: 0;
}

.phx-head__title {
  position: relative;
  display: inline-block;
  max-width: 100%;
  margin: 0;
  color: var(--phx-ink);
  font-size: clamp(24px, 4.2vw, 38px);
  font-weight: var(--yg-font-weight-bold);
  line-height: 1.1;
  letter-spacing: 0.02em;
  overflow-wrap: anywhere;
  animation: phx-slide-in 420ms 60ms var(--phx-ease) both;
}

.phx-head__title::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 0;
  width: 44px;
  height: 3px;
  background: var(--phx-accent);
  transform: skewX(-40deg);
  transform-origin: left;
}

.phx-head__lead {
  max-width: 54ch;
  margin: var(--yg-space-3) 0 0;
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  line-height: var(--yg-line-height-relaxed);
  animation: phx-slide-in 420ms 110ms var(--phx-ease) both;
}

.phx-head__index {
  position: absolute;
  top: 10px;
  right: 20px;
  z-index: 0;
  color: transparent;
  font: 400 clamp(60px, 10vw, 104px) / 0.8 var(--phx-display);
  -webkit-text-stroke: 1px color-mix(in srgb, var(--phx-ink) 42%, transparent);
  text-shadow:
    4px 0 color-mix(in srgb, var(--phx-accent-2) 26%, transparent),
    -3px 0 color-mix(in srgb, var(--phx-accent) 30%, transparent);
  opacity: 0.85;
  user-select: none;
  pointer-events: none;
  animation: phx-slide-in-r 450ms 40ms var(--phx-ease) both;
}

.phx-ban-tag {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  border-radius: 0;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%);
  overflow-wrap: anywhere;
  white-space: normal;
  height: auto;
  padding-block: 2px;
}

/* 页签 + 权限按钮：独立一行，390 下自动换行不撑破页头 */
.phx-page-head :deep(.el-page-header__extra) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--yg-space-2);
  width: 100%;
  margin-top: var(--yg-space-4);
  padding-top: var(--yg-space-3);
  border-top: 1px solid var(--phx-line-soft);
}

.phx-seg {
  --el-segmented-color: var(--phx-muted);
  --el-segmented-bg-color: transparent;
  --el-segmented-padding: 0;
  --el-segmented-item-selected-color: var(--phx-accent);
  --el-segmented-item-selected-bg-color: transparent;
  --el-segmented-item-hover-color: var(--phx-ink);
  --el-segmented-item-hover-bg-color: color-mix(in srgb, var(--phx-accent) 9%, transparent);
  --el-segmented-item-active-bg-color: color-mix(in srgb, var(--phx-accent) 16%, transparent);
  flex: 1 1 320px;
  min-width: 0;
  max-width: 100%;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: inset 0 -1px 0 var(--phx-line-soft);
}

.phx-seg :deep(.el-segmented__group) {
  width: 100%;
  gap: 0;
}

.phx-seg :deep(.el-segmented__item) {
  flex: 1 1 0;
  min-width: 0;
  padding: 0 8px;
  border-radius: 0;
  clip-path: none;
}

/* 选中态只画底部 3px 强调线，不再铺整块斜切背景 */
.phx-seg :deep(.el-segmented__item-selected) {
  border-radius: 0;
  background: linear-gradient(var(--phx-accent), var(--phx-accent)) left bottom / 100% 3px no-repeat;
  clip-path: none;
  box-shadow: none;
}

.phx-tab-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  padding: 5px 0;
}

.phx-tab-icon {
  flex: 0 0 auto;
}

.phx-tab-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--yg-font-size-sm);
  font-weight: var(--yg-font-weight-medium);
}

.phx-actions {
  display: flex;
  flex: 0 1 auto;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  margin-left: auto;
}

.phx-action {
  display: inline-flex;
}

.phx-actions :deep(.el-button) {
  border: 0;
  border-radius: 0;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%);
  transition:
    transform 240ms var(--phx-spring),
    color 180ms var(--phx-ease),
    background-color 180ms var(--phx-ease);
}

/* 文字型操作不要切角外框 */
.phx-actions :deep(.el-button.is-text),
.phx-actions :deep(.el-button.is-link) {
  clip-path: none;
}

.phx-actions :deep(.el-button:hover:not(.is-disabled)) {
  transform: translateX(2px) skewX(-5deg);
}

/* ---------- 封禁提示：直角 + 右上斜切 ---------- */
.phx-alert {
  position: relative;
  margin: 0;
  border-radius: 0;
  clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 0 100%);
  box-shadow: 4px 4px 0 var(--phx-hard);
  animation: phx-panel-in 380ms 80ms var(--phx-ease) backwards;
}

.phx-alert :deep(.el-alert__title) {
  font-family: var(--phx-hud), var(--yg-font-sans);
  letter-spacing: 0.03em;
}

/* ---------- 资料块：切角面板 + HUD 描述表 ---------- */
.phx-panel {
  --phx-cut: 18px;
  --phx-cut-s: 8px;
  position: relative;
  isolation: isolate;
  min-width: 0;
  color: var(--yg-text-secondary);
  filter: drop-shadow(5px 5px 0 var(--phx-hard));
  animation: phx-panel-in 440ms calc(var(--phx-i, 0) * 55ms + 60ms) var(--phx-ease) backwards;
}

.phx-panel::before,
.phx-panel::after {
  content: '';
  position: absolute;
  z-index: -1;
  pointer-events: none;
  clip-path: polygon(
    0 0,
    calc(100% - var(--phx-cut)) 0,
    100% var(--phx-cut),
    100% 100%,
    var(--phx-cut-s) 100%,
    0 calc(100% - var(--phx-cut-s))
  );
}

.phx-panel::before {
  inset: 0;
  background:
    linear-gradient(
      225deg,
      var(--phx-accent) 0 calc(var(--phx-cut) * 0.9),
      transparent calc(var(--phx-cut) * 0.9 + 1px)
    ),
    color-mix(in srgb, var(--phx-ink) 16%, transparent);
}

.phx-panel::after {
  inset: 1px;
  background:
    linear-gradient(var(--phx-accent), transparent 70%) left top / 2px 100% no-repeat,
    linear-gradient(
      135deg,
      color-mix(in srgb, var(--phx-accent) 5%, var(--phx-surface)),
      var(--phx-surface) 46%
    );
}

.phx-member {
  padding: var(--yg-space-4) calc(var(--phx-cut) + var(--yg-space-4)) var(--yg-space-5)
    var(--yg-space-5);
  --el-descriptions-item-bordered-label-background: var(--phx-sunken);
  --el-descriptions-table-border: 1px solid var(--phx-line-soft);
}

.phx-member :deep(.el-card) {
  --el-card-bg-color: transparent;
  --el-card-border-color: transparent;
  --el-card-padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.phx-member :deep(.el-card__body) {
  padding: 0;
}

.phx-primary-device {
  display: flex;
  align-items: baseline;
  gap: var(--yg-space-2);
  margin: 0 0 var(--yg-space-3);
  padding: 0 0 var(--yg-space-2);
  border-bottom: 1px dashed var(--phx-line);
  color: var(--phx-muted);
  font-size: var(--yg-font-size-sm);
  overflow-wrap: anywhere;
}

.phx-primary-device::before {
  content: 'DEV';
  flex: 0 0 auto;
  padding: 1px 5px;
  border: 1px solid var(--phx-line);
  color: var(--phx-accent);
  font: 400 11px / 1.1 var(--phx-display);
  letter-spacing: 0.16em;
}

.phx-member :deep(.el-descriptions__body) {
  background: transparent;
}

.phx-member :deep(.el-descriptions__table) {
  border-collapse: collapse;
  table-layout: fixed;
}

.phx-member :deep(.el-descriptions__cell) {
  border-color: var(--phx-line-soft);
  color: var(--phx-ink);
  overflow-wrap: anywhere;
  animation: phx-cell-in 360ms var(--phx-ease) backwards;
  animation-delay: var(--phx-desc-delay, 0ms);
}

.phx-member :deep(.el-descriptions__label) {
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: var(--yg-font-size-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.phx-member :deep(.el-descriptions__content) {
  color: var(--yg-text-secondary);
}

.phx-member :deep(.el-descriptions__table tr:nth-child(2) .el-descriptions__cell) {
  --phx-desc-delay: 40ms;
}

.phx-member :deep(.el-descriptions__table tr:nth-child(3) .el-descriptions__cell) {
  --phx-desc-delay: 80ms;
}

.phx-member :deep(.el-descriptions__table tr:nth-child(4) .el-descriptions__cell) {
  --phx-desc-delay: 120ms;
}

.phx-member :deep(.el-descriptions__table tr:nth-child(n + 5) .el-descriptions__cell) {
  --phx-desc-delay: 160ms;
}

.phx-member :deep(.el-tag) {
  border-radius: 0;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%);
}

/* ---------- 活动列表：硬边卡片 + 细线表格 + 固定列不透明 ---------- */
.phx-activity-pane {
  min-width: 0;
}

.phx-activity > :deep(.el-card) {
  --el-card-bg-color: var(--phx-surface);
  --el-card-border-color: color-mix(in srgb, var(--phx-ink) 16%, transparent);
  --el-card-padding: var(--yg-space-4) var(--yg-space-4) var(--yg-space-2);
  position: relative;
  overflow: hidden;
  border-radius: 0;
  box-shadow: 5px 5px 0 var(--phx-hard);
  animation: phx-card-in 440ms 100ms var(--phx-ease) backwards;
}

.phx-activity > :deep(.el-card:hover) {
  box-shadow: 5px 5px 0 var(--phx-hard);
}

/* 角标并入 ::before（左侧强调条 + 右上折角），把 ::after 让给全局流光边 */
.phx-activity > :deep(.el-card)::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 3;
  background:
    linear-gradient(
        225deg,
        var(--phx-accent) 0 9px,
        transparent 9px 12px,
        var(--phx-line) 12px 13px,
        transparent 13px
      )
      right top / 22px 22px no-repeat,
    linear-gradient(var(--phx-accent), transparent 72%) left top / 2px 100% no-repeat;
  pointer-events: none;
}

.phx-activity :deep(.el-button) {
  border-radius: 0;
}

.phx-activity :deep(.el-table) {
  --el-table-header-bg-color: var(--phx-sunken);
  --el-fill-color-lighter: color-mix(in srgb, var(--phx-ink) 3%, var(--phx-surface));
  --phx-row-hover: color-mix(in srgb, var(--phx-accent) 9%, var(--phx-surface));
  margin-top: var(--yg-space-3);
  border-top: 1px solid var(--phx-line);
}

.phx-activity :deep(.el-table__inner-wrapper::before) {
  background-color: var(--phx-line-soft);
}

.phx-activity :deep(.el-table thead th.el-table__cell) {
  background-color: var(--phx-sunken);
  border-bottom: 1px solid var(--phx-line);
  color: var(--phx-muted);
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: var(--yg-font-size-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.phx-activity :deep(.el-table thead th.el-table__cell:first-child) {
  background-image: linear-gradient(var(--phx-accent), var(--phx-accent));
  background-position: left center;
  background-size: 2px 40%;
  background-repeat: no-repeat;
}

.phx-activity :deep(.el-table .el-table__row > td.el-table__cell) {
  transition: background-color 180ms var(--phx-ease);
}

.phx-activity :deep(.el-table .el-table__row > td.el-table__cell .cell) {
  transition: transform 260ms var(--phx-spring);
}

.phx-activity :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell),
.phx-activity :deep(.el-table .el-table__body tr.el-table__row.hover-row > td.el-table__cell) {
  background-color: var(--phx-row-hover);
  box-shadow: inset 0 -1px 0 var(--phx-line);
}

.phx-activity
  :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell:first-child) {
  background-image: linear-gradient(
    105deg,
    color-mix(in srgb, var(--phx-accent) 26%, transparent) 0 46%,
    transparent 46%
  );
  box-shadow:
    inset 3px 0 0 var(--phx-accent),
    inset 0 -1px 0 var(--phx-line);
}

.phx-activity :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell .cell) {
  transform: translateX(3px) skewX(-3deg);
}

/* 固定列 sticky 单元格必须保持不透明，避免横向滚动时内容透出 */
.phx-activity
  :deep(
    .el-table .el-table__body tr.el-table__row > td.el-table__cell.el-table-fixed-column--right
  ) {
  background-color: var(--phx-surface);
}

.phx-activity
  :deep(
    .el-table
      .el-table__body
      tr.el-table__row--striped
      > td.el-table__cell.el-table-fixed-column--right
  ) {
  background-color: var(--el-fill-color-lighter);
}

.phx-activity
  :deep(
    .el-table
      .el-table__body
      tr.el-table__row:hover
      > td.el-table__cell.el-table-fixed-column--right
  ) {
  background-color: var(--phx-row-hover);
}

/* 行进入：只动单元格，tr 上做位移会打断 sticky 固定列 */
.phx-activity :deep(.el-table__body tr.el-table__row > td.el-table__cell) {
  animation: phx-cell-in 360ms var(--phx-ease) backwards;
  animation-delay: var(--phx-row-delay, 0ms);
}

.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(2)) {
  --phx-row-delay: 30ms;
}
.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(3)) {
  --phx-row-delay: 60ms;
}
.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(4)) {
  --phx-row-delay: 90ms;
}
.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(5)) {
  --phx-row-delay: 120ms;
}
.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(6)) {
  --phx-row-delay: 150ms;
}
.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(7)) {
  --phx-row-delay: 175ms;
}
.phx-activity :deep(.el-table__body tr.el-table__row:nth-of-type(n + 8)) {
  --phx-row-delay: 200ms;
}

.phx-activity :deep(.el-table .el-input__wrapper) {
  border-radius: 0;
  background-color: var(--phx-surface);
  box-shadow:
    inset 0 0 0 1px var(--phx-line-soft),
    inset 0 -2px 0 var(--phx-line);
}

.phx-activity :deep(.el-table .el-input__wrapper:hover) {
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--phx-ink) 22%, transparent),
    inset 0 -2px 0 var(--phx-accent);
}

.phx-activity :deep(.el-table .el-input__wrapper.is-focus) {
  box-shadow:
    inset 0 0 0 1px var(--phx-accent),
    inset 0 -2px 0 var(--phx-accent);
}

.phx-activity :deep(.el-tag) {
  border-radius: 0;
  font-weight: var(--yg-font-weight-medium);
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%);
}

html[data-theme='light'] .phx-activity :deep(.el-tag) {
  color: var(--phx-accent);
  border-color: color-mix(in srgb, var(--phx-accent) 30%, transparent);
  background-color: color-mix(in srgb, var(--phx-accent) 8%, transparent);
}

html[data-theme='light'] .phx-activity :deep(.el-tag.el-tag--success) {
  color: var(--yg-text-success);
  border-color: color-mix(in srgb, var(--yg-text-success) 30%, transparent);
  background-color: var(--yg-color-success-soft);
}

html[data-theme='light'] .phx-activity :deep(.el-tag.el-tag--warning) {
  color: var(--yg-text-warning);
  border-color: color-mix(in srgb, var(--yg-text-warning) 30%, transparent);
  background-color: var(--yg-color-warning-soft);
}

html[data-theme='light'] .phx-activity :deep(.el-tag.el-tag--danger) {
  color: var(--yg-text-danger);
  border-color: color-mix(in srgb, var(--yg-text-danger) 30%, transparent);
  background-color: var(--yg-color-danger-soft);
}

/* 分页：几何与颜色交给共享组件，这里只补字体 */
.phx-activity :deep(.yg-pg) {
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-weight: 500;
  letter-spacing: 0.03em;
}

.phx-activity :deep(.yg-pg__num) {
  font-family: var(--phx-hud), var(--yg-font-sans);
  font-size: 14px;
  font-weight: 700;
}

/* ---------- 修改 / 日志：同一套切角容器 ---------- */
.phx-content {
  padding: var(--yg-space-4) calc(var(--phx-cut) + var(--yg-space-4)) var(--yg-space-5)
    var(--yg-space-5);
  overflow-wrap: anywhere;
}

.phx-content :deep(.el-card) {
  border-radius: 0;
  box-shadow: none;
}

.phx-content :deep(.el-button),
.phx-content :deep(.el-tag) {
  border-radius: 0;
}

/* ---------- 弹窗触发区：密码块改为直角 + 左下斜切 ---------- */
.phx-code-block {
  flex: 1;
  min-width: 0;
  padding: 8px 12px;
  border-left: 2px solid var(--phi-cyan, var(--yg-color-primary));
  background: var(--yg-bg-surface-sunken);
  color: var(--yg-text-primary);
  font-family: var(--yg-font-mono, ui-monospace, monospace);
  font-size: var(--yg-font-size-lg);
  font-weight: var(--yg-font-weight-bold);
  letter-spacing: 0.18em;
  overflow-wrap: anywhere;
  clip-path: polygon(0 0, calc(100% - 8px) 0, 100% 8px, 100% 100%, 8px 100%, 0 calc(100% - 8px));
}

/* ---------- 动效 ---------- */
@keyframes phx-head-in {
  from {
    opacity: 0;
    clip-path: polygon(0 0, 0 0, 0 22px, 0 100%, 0 100%);
  }
  to {
    opacity: 1;
    clip-path: polygon(0 0, calc(100% - 22px) 0, 100% 22px, 100% 100%, 0 100%);
  }
}

@keyframes phx-slide-in {
  from {
    opacity: 0;
    transform: translateX(-14px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes phx-slide-in-r {
  from {
    opacity: 0;
    transform: translateX(18px) skewX(-8deg);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes phx-panel-in {
  from {
    opacity: 0;
    transform: translateX(-10px);
    clip-path: polygon(
      -12px -12px,
      -12px -12px,
      calc(-12px - 40px) calc(100% + 12px),
      -52px calc(100% + 12px)
    );
  }
  to {
    opacity: 1;
    transform: none;
    clip-path: polygon(
      -12px -12px,
      calc(100% + 52px) -12px,
      calc(100% + 12px) calc(100% + 12px),
      -52px calc(100% + 12px)
    );
  }
}

@keyframes phx-card-in {
  from {
    opacity: 0;
    clip-path: polygon(-12px -12px, -12px -12px, -52px calc(100% + 12px), -52px calc(100% + 12px));
  }
  to {
    opacity: 1;
    clip-path: polygon(
      -12px -12px,
      calc(100% + 52px) -12px,
      calc(100% + 12px) calc(100% + 12px),
      -52px calc(100% + 12px)
    );
  }
}

@keyframes phx-cell-in {
  from {
    opacity: 0;
    clip-path: inset(0 100% 0 0);
  }
  to {
    opacity: 1;
    clip-path: inset(0 0 0 0);
  }
}

/* ---------- 窄屏（390）：页头收紧、按钮换行、编号缩小 ---------- */
@media (max-width: 599px) {
  .phx-page-head {
    padding: var(--yg-space-4);
  }

  .phx-page-head :deep(.el-page-header__content) {
    padding-right: 58px;
    padding-left: var(--yg-space-2);
  }

  .phx-head__index {
    top: 8px;
    right: 12px;
    font-size: 54px;
  }

  .phx-page-head::after {
    right: 12px;
    width: 72px;
  }

  .phx-page-head :deep(.el-page-header__extra) {
    gap: 6px;
  }

  .phx-seg {
    flex-basis: 100%;
  }

  .phx-seg :deep(.el-segmented__item) {
    padding: 0 4px;
  }

  .phx-tab-label {
    font-size: var(--yg-font-size-xs);
  }

  .phx-actions {
    width: 100%;
    margin-left: 0;
  }

  .phx-member,
  .phx-content {
    padding: var(--yg-space-4) var(--yg-space-3) var(--yg-space-4) var(--yg-space-4);
  }

  .phx-member :deep(.el-descriptions__table) {
    table-layout: auto;
  }

  .phx-activity > :deep(.el-card) {
    --el-card-padding: var(--yg-space-3) var(--yg-space-2) var(--yg-space-1);
    box-shadow: 3px 3px 0 var(--phx-hard);
  }

  .phx-alert {
    box-shadow: 3px 3px 0 var(--phx-hard);
  }
}

@media (prefers-reduced-motion: reduce) {
  .phx-page-head,
  .phx-head__eyebrow,
  .phx-head__title,
  .phx-head__lead,
  .phx-head__index,
  .phx-alert,
  .phx-panel,
  .phx-activity > :deep(.el-card),
  .phx-member :deep(.el-descriptions__cell),
  .phx-activity :deep(.el-table__body tr.el-table__row > td.el-table__cell) {
    animation: none;
  }

  .phx-page-head :deep(.el-page-header__back),
  .phx-actions :deep(.el-button),
  .phx-activity :deep(.el-table .el-table__row > td.el-table__cell),
  .phx-activity :deep(.el-table .el-table__row > td.el-table__cell .cell) {
    transition: none;
  }

  .phx-page-head :deep(.el-page-header__back:hover),
  .phx-actions :deep(.el-button:hover:not(.is-disabled)),
  .phx-activity :deep(.el-table .el-table__body tr.el-table__row:hover > td.el-table__cell .cell) {
    transform: none;
  }
}
</style>
