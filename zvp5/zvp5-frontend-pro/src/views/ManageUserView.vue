<script setup lang="ts">
// Admin / department / secretary user page for the Pro console.
//
// Replaces the old read-only modal: the URL is shareable (`/manage/users/:id`),
// profile/time/activity reads stay the same, and admins get explicit write
// affordances (roles, student id, class). Secretaries are read-only and may only
// issue a one-time temp password for a classmate.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminWriteVerification from '@/components/AdminWriteVerification.vue'
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { ApiError, currentUserId } from '@/api/http'
import { fetchGroups } from '@/api/admin-manage'
import {
  ADMIN_EDITABLE_ROLES,
  clearUserClass,
  createUserTempPassword,
  fetchAdminGroupName,
  fetchAdminUserActivities,
  fetchAdminUserProfile,
  fetchAdminUserTime,
  setUserClass,
  setUserBanned,
  updateUserRoles,
  updateUserStudentId,
  type AdminEditableRole,
  type AdminUserActivity,
  type AdminUserBanResult,
  type AdminUserProfile,
  type AdminUserTime,
  type TempPasswordResult
} from '@/api/admin-user-detail'
import { currentRoles } from '@/api/permissions'
import { useSessionStore } from '@/shared/stores/session'

const ACTIVITY_PAGE_SIZE = 5
const HOUR_ROWS = [
  { key: 'on-campus', label: '校内' },
  { key: 'off-campus', label: '校外' },
  { key: 'social-practice', label: '社会实践' }
] as const

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  department: '学生会',
  secretary: '团支书',
  student: '学生',
  auditor: '审计员',
  teacher: '教师',
  system: '系统'
}

const ACTIVITY_TYPES: Record<string, string> = {
  'on-campus': '校内',
  'off-campus': '校外',
  'social-practice': '社会实践',
  hybrid: '混合'
}

const BAN_REASON_MAX = 200
const BAN_DURATION_MAX = 36500
const BAN_DURATION_PRESETS = [1, 3, 7, 14, 30] as const
type BanDurationChoice = 'permanent' | 'custom' | `${number}`

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const adminVerification = ref<InstanceType<typeof AdminWriteVerification>>()

const operatorRoles = currentRoles()
const isAdminUser = operatorRoles.includes('admin')
const isDepartmentUser = operatorRoles.includes('department')
const isSecretaryUser = operatorRoles.includes('secretary') && !isAdminUser && !isDepartmentUser
const canEditClass = isAdminUser || isDepartmentUser

const userId = computed(() => String(route.params.id ?? ''))

const profile = ref<AdminUserProfile | null>(null)
const className = ref('')
const time = ref<AdminUserTime | null>(null)

const profileLoading = ref(true)
const profileError = ref('')
const notFound = ref(false)

const timeLoading = ref(true)
const timeError = ref('')

const activities = ref<AdminUserActivity[]>([])
const activitiesTotal = ref(0)
const activitiesPage = ref(1)
const activitiesLoading = ref(true)
const activitiesLoaded = ref(false)
const activitiesError = ref('')

const roleDraft = ref<string[]>([])
const rolesOriginal = ref<string[]>([])
const studentIdDraft = ref('')
const studentIdOriginal = ref('')
const classDraft = ref('')
const classOriginal = ref('')

const classOptions = ref<{ _id: string; name: string; type?: string }[]>([])
const classOptionsLoading = ref(false)
const classOptionsError = ref('')

const saving = ref(false)
const saveNotice = ref('')
const saveError = ref('')

const tempPassword = ref<TempPasswordResult | null>(null)
const tempPasswordBusy = ref(false)
const tempPasswordError = ref('')
const tempPasswordCopied = ref(false)

const banDialog = ref<HTMLDialogElement>()
const banMode = ref<'ban' | 'unban'>('ban')
const banReason = ref('')
const banDurationChoice = ref<BanDurationChoice>('7')
const banCustomDays = ref('')
const banSubmitting = ref(false)
const banError = ref('')
const banNotice = ref('')

// Stale-response guard for the profile/time/activity reads.
let runId = 0

const displayRoles = computed(() => {
  const roles = (profile.value?.roles ?? []).filter(
    (role): role is string => typeof role === 'string' && role.length > 0
  )
  if (!roles.length) return ['学生']
  return Array.from(new Set(roles)).map((role) => ROLE_LABELS[role] ?? role)
})

const targetIsAdmin = computed(() => (profile.value?.roles ?? []).includes('admin'))

const unsupportedRoles = computed(() =>
  (profile.value?.roles ?? []).filter(
    (role) => !(ADMIN_EDITABLE_ROLES as readonly string[]).includes(role)
  )
)

const isSelf = computed(() => {
  const target = profile.value?._id
  const operator = currentUserId()
  return Boolean(target && operator && target === operator)
})

const sameClass = computed(() => {
  const operatorClass = session.profile?.class_id || ''
  const targetClass = profile.value?.class_id || ''
  return Boolean(operatorClass && targetClass && operatorClass === targetClass)
})

const rolesEditable = computed(
  () => isAdminUser && !isSelf.value && !targetIsAdmin.value && unsupportedRoles.value.length === 0
)
const studentIdEditable = computed(() => isAdminUser && !isSelf.value)

const roleEditingBlockedReason = computed(() => {
  if (!isAdminUser || rolesEditable.value) return ''
  if (isSelf.value) return '不能修改自己的角色。'
  if (targetIsAdmin.value) return '后端禁止修改管理员账号的角色。'
  if (unsupportedRoles.value.length) {
    return `该账号包含本页不可提交的角色（${unsupportedRoles.value.join('、')}），请到主站处理。`
  }
  return ''
})

const studentIdBlockedReason = computed(() => {
  if (!isAdminUser || studentIdEditable.value) return ''
  if (isSelf.value) return '不能在这里修改自己的学号：改学号会让当前登录立即失效。'
  return ''
})

const classChoices = computed(() => {
  const list = [...classOptions.value]
  if (classOriginal.value && !list.some((group) => group._id === classOriginal.value)) {
    list.unshift({
      _id: classOriginal.value,
      name: className.value || classOriginal.value,
      type: 'class'
    })
  }
  return list
})

function rolesEqual(left: string[], right: string[]): boolean {
  const a = [...left].sort()
  const b = [...right].sort()
  return a.length === b.length && a.every((value, index) => value === b[index])
}

const roleChanged = computed(
  () => rolesEditable.value && !rolesEqual(roleDraft.value, rolesOriginal.value)
)
const studentIdChanged = computed(
  () =>
    studentIdEditable.value && studentIdDraft.value.trim() !== studentIdOriginal.value.trim()
)
const classChanged = computed(
  () => canEditClass && (classDraft.value || '') !== (classOriginal.value || '')
)
const hasChanges = computed(
  () => roleChanged.value || studentIdChanged.value || classChanged.value
)
const invalidatesTargetSession = computed(
  () => roleChanged.value || studentIdChanged.value
)

const canIssueTempPassword = computed(() => {
  if (!profile.value) return false
  if (isAdminUser || isDepartmentUser) return true
  return isSecretaryUser && sameClass.value
})

const banSummary = computed(() => {
  const target = profile.value
  if (!target?.banned) return ''
  if (target.ban_permanent) return '永久封禁'
  const raw = target.ban_until
  if (!raw) return '封禁中'
  return `封禁至 ${formatDateTime(raw)}`
})

/** Ban/unban is admin-only; the backend owns every other permission gate. */
const canModerateBan = computed(() => {
  const target = profile.value
  if (!isAdminUser || !target) return false
  // Another admin account is out of scope even for unban (the backend allows
  // the unban direction, but this page deliberately does not surface it).
  if (targetIsAdmin.value && !isSelf.value) return false
  // Own account: only an already-banned operator gets the unban action.
  if (isSelf.value && !target.banned) return false
  return true
})

const banActionLabel = computed(() => (profile.value?.banned ? '解封账号' : '封禁账号'))

const banReasonLength = computed(() => banReason.value.trim().length)
const banReasonInvalid = computed(
  () => banMode.value === 'ban' && (banReasonLength.value < 1 || banReasonLength.value > BAN_REASON_MAX)
)
const banCustomDaysValue = computed(() => Number(banCustomDays.value))
const banCustomDaysValid = computed(
  () =>
    Number.isInteger(banCustomDaysValue.value) &&
    banCustomDaysValue.value >= 1 &&
    banCustomDaysValue.value <= BAN_DURATION_MAX
)
const banDurationInvalid = computed(
  () => banMode.value === 'ban' && banDurationChoice.value === 'custom' && !banCustomDaysValid.value
)
const banCanSubmit = computed(() => {
  if (!profile.value || banSubmitting.value) return false
  if (banMode.value === 'unban') return true
  return (
    banReasonLength.value >= 1 &&
    banReasonLength.value <= BAN_REASON_MAX &&
    !banDurationInvalid.value
  )
})

const totalHours = computed(() =>
  HOUR_ROWS.reduce((sum, row) => sum + hoursOf(row.key), 0)
)
const hourRows = computed(() =>
  HOUR_ROWS.map((row) => ({ label: row.label, value: hoursOf(row.key) }))
)
const activityPages = computed(() =>
  Math.max(1, Math.ceil(activitiesTotal.value / ACTIVITY_PAGE_SIZE))
)

function hoursOf(key: (typeof HOUR_ROWS)[number]['key']): number {
  const value = Number(time.value?.[key] ?? 0)
  return Number.isFinite(value) ? Math.max(0, value) : 0
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatDateTime(value: Date | string | null | undefined): string {
  if (!value) return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatDate(value: string | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function formatHours(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, number).toFixed(1) : '0.0'
}

function readError(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return '当前账号没有查看该用户资料的权限。'
    if (reason.status === 404) return '用户不存在或已被删除。'
    return reason.message || fallback
  }
  return fallback
}

function actionError(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return reason.message || '当前账号无权执行该操作。'
    if (reason.status === 409) return reason.message || '目标值已被占用。'
    if (reason.status === 400 || reason.status === 422) {
      return reason.message || '提交的数据不合法。'
    }
    return reason.message || fallback
  }
  return reason instanceof Error ? reason.message : fallback
}

function isNotFound(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 404
}

function stale(run: number): boolean {
  return run !== runId
}

function seedEditForm() {
  const target = profile.value
  roleDraft.value = (target?.roles ?? []).filter((role): role is AdminEditableRole =>
    (ADMIN_EDITABLE_ROLES as readonly string[]).includes(role)
  )
  rolesOriginal.value = [...roleDraft.value]
  studentIdDraft.value = String(target?.id ?? '')
  studentIdOriginal.value = studentIdDraft.value
  classDraft.value = target?.class_id || ''
  classOriginal.value = classDraft.value
}

async function loadProfile(run: number) {
  profileLoading.value = true
  profileError.value = ''
  notFound.value = false
  try {
    const data = await fetchAdminUserProfile(userId.value)
    if (stale(run)) return
    if (!data?._id) {
      notFound.value = true
      return
    }
    profile.value = data
    seedEditForm()
    if (data.class_id) {
      void fetchAdminGroupName(data.class_id).then((name) => {
        if (!stale(run)) className.value = name
      })
    }
  } catch (reason) {
    if (stale(run)) return
    if (isNotFound(reason)) {
      notFound.value = true
      return
    }
    profileError.value = readError(reason, '用户资料加载失败，请稍后重试。')
  } finally {
    if (!stale(run)) profileLoading.value = false
  }
}

async function loadTime(run: number) {
  timeLoading.value = true
  timeError.value = ''
  try {
    const data = await fetchAdminUserTime(userId.value)
    if (stale(run)) return
    time.value = data ?? null
  } catch (reason) {
    if (stale(run)) return
    time.value = null
    timeError.value = readError(reason, '个人时长加载失败，请稍后重试。')
  } finally {
    if (!stale(run)) timeLoading.value = false
  }
}

async function loadActivities(run: number, page: number, revertTo?: number) {
  activitiesLoading.value = true
  activitiesError.value = ''
  try {
    const data = await fetchAdminUserActivities(userId.value, page, ACTIVITY_PAGE_SIZE)
    if (stale(run)) return
    activities.value = Array.isArray(data?.activities) ? data.activities : []
    const total = Number(data?.total)
    activitiesTotal.value = Number.isFinite(total) ? Math.max(0, total) : 0
    activitiesPage.value = page
    activitiesLoaded.value = true
  } catch (reason) {
    if (stale(run)) return
    if (revertTo !== undefined) activitiesPage.value = revertTo
    activitiesError.value = readError(reason, '活动记录加载失败，请稍后重试。')
  } finally {
    if (!stale(run)) activitiesLoading.value = false
  }
}

async function loadClassOptions() {
  if (!canEditClass || classOptionsLoading.value) return
  classOptionsLoading.value = true
  classOptionsError.value = ''
  try {
    const data = await fetchGroups(1, 100, '')
    classOptions.value = (Array.isArray(data.data) ? data.data : [])
      .filter((group) => group.type !== 'permission')
      .map((group) => ({ _id: group._id, name: group.name, type: group.type }))
  } catch (reason) {
    classOptions.value = []
    classOptionsError.value = actionError(reason, '班级列表读取失败，请稍后重试。')
  } finally {
    classOptionsLoading.value = false
  }
}

function start() {
  runId += 1
  const run = runId

  profile.value = null
  className.value = ''
  profileError.value = ''
  notFound.value = false
  profileLoading.value = true

  time.value = null
  timeError.value = ''
  timeLoading.value = true

  activities.value = []
  activitiesTotal.value = 0
  activitiesPage.value = 1
  activitiesError.value = ''
  activitiesLoaded.value = false
  activitiesLoading.value = true

  saveError.value = ''
  saveNotice.value = ''
  tempPassword.value = null
  tempPasswordError.value = ''
  tempPasswordCopied.value = false
  banNotice.value = ''
  banError.value = ''
  closeBanDialog(true)

  void loadProfile(run)
  void loadTime(run)
  void loadActivities(run, 1)
}

function retryProfile() {
  if (profileLoading.value) return
  start()
}

function retryTime() {
  if (timeLoading.value) return
  void loadTime(runId)
}

function goActivities(delta: number) {
  if (activitiesLoading.value) return
  const target = activitiesPage.value + delta
  if (target < 1 || target > activityPages.value) return
  void loadActivities(runId, target, activitiesPage.value)
}

function backToList() {
  const query: Record<string, string> = { tab: 'users' }
  for (const key of ['q', 'role', 'page']) {
    const value = route.query[key]
    if (typeof value === 'string' && value) query[key] = value
  }
  void router.push({ name: 'manage', query })
}

async function saveChanges() {
  const target = profile.value
  if (!target?._id || saving.value || !hasChanges.value) return

  saving.value = true
  saveError.value = ''
  saveNotice.value = ''
  const applied: string[] = []

  try {
    if (roleChanged.value) {
      await updateUserRoles(target._id, roleDraft.value)
      applied.push('角色')
    }
    if (studentIdChanged.value) {
      await updateUserStudentId(target._id, studentIdDraft.value.trim())
      applied.push('学号')
    }
    if (classChanged.value) {
      const nextClass = classDraft.value
      const oldClass = classOriginal.value
      if (nextClass) {
        await setUserClass(nextClass, target._id)
      } else if (oldClass) {
        await clearUserClass(oldClass, target._id)
      }
      applied.push('班级')
    }

    const sessionWarning = invalidatesTargetSession.value
      ? ' 角色 / 学号变更已使该用户当前会话失效，需要重新登录。'
      : ''
    saveNotice.value = `已更新${applied.join('、')}。${sessionWarning}`
    await loadProfile(runId)
  } catch (reason) {
    const partial = applied.length ? `已生效：${applied.join('、')}。` : ''
    saveError.value = `${partial}${actionError(reason, '保存失败，请稍后重试。')}`
    // A partial write may have changed the profile; refresh so the form shows
    // the server truth instead of a half-applied draft.
    if (applied.length) void loadProfile(runId)
  } finally {
    saving.value = false
  }
}

async function issueTempPassword() {
  const target = profile.value
  if (!target?._id || tempPasswordBusy.value) return

  tempPasswordError.value = ''
  tempPasswordCopied.value = false

  // The operator proves their own password; only the short verify token goes
  // to the temp-password endpoint.
  const proof = await adminVerification.value?.request()
  if (!proof) return

  tempPasswordBusy.value = true
  tempPassword.value = null
  try {
    tempPassword.value = await createUserTempPassword(target._id, proof)
  } catch (reason) {
    tempPasswordError.value = actionError(reason, '临时密码签发失败，请稍后重试。')
  } finally {
    tempPasswordBusy.value = false
  }
}

async function copyTempPassword() {
  const code = tempPassword.value?.code
  if (!code) return
  try {
    await navigator.clipboard.writeText(code)
    tempPasswordCopied.value = true
  } catch {
    tempPasswordCopied.value = false
    tempPasswordError.value = '复制失败，请手动选择上方密码复制。'
  }
}

function openBanDialog() {
  const target = profile.value
  if (!canModerateBan.value || !target) return
  banMode.value = target.banned ? 'unban' : 'ban'
  banReason.value = ''
  banDurationChoice.value = '7'
  banCustomDays.value = ''
  banError.value = ''
  banSubmitting.value = false
  const dialog = banDialog.value
  if (!dialog) return
  if (typeof dialog.showModal === 'function') {
    if (!dialog.open) dialog.showModal()
  } else {
    // Very old tablets lack `showModal`; fall back to the open attribute.
    dialog.setAttribute('open', '')
  }
}

function closeBanDialog(force = false) {
  if (banSubmitting.value && !force) return
  const dialog = banDialog.value
  if (!dialog) return
  if (typeof dialog.close === 'function' && dialog.open) dialog.close()
  else dialog.removeAttribute('open')
}

function onBanBackdrop(event: MouseEvent) {
  if (event.target === banDialog.value) closeBanDialog()
}

function applyBanResult(result: AdminUserBanResult, reason: string | null) {
  const target = profile.value
  if (!target) return
  target.banned = result.banned
  target.ban_until = result.ban_until
  target.ban_permanent = result.permanent
  target.ban_reason = result.banned ? reason ?? target.ban_reason ?? null : null
}

async function submitBan() {
  const target = profile.value
  if (!target?._id || !banCanSubmit.value) return

  banSubmitting.value = true
  banError.value = ''
  banNotice.value = ''

  try {
    if (banMode.value === 'unban') {
      const result = await setUserBanned(target._id, { banned: false })
      applyBanResult(result, null)
      banNotice.value = '已解封该账号，目标用户可以重新登录。'
    } else {
      const reason = banReason.value.trim()
      const duration =
        banDurationChoice.value === 'permanent'
          ? undefined
          : banDurationChoice.value === 'custom'
            ? banCustomDaysValue.value
            : Number(banDurationChoice.value)
      const result = await setUserBanned(target._id, {
        banned: true,
        reason,
        ...(duration === undefined ? {} : { duration_days: duration })
      })
      applyBanResult(result, reason)
      banNotice.value =
        result.permanent || !result.ban_until
          ? '已永久封禁该账号，目标会话已失效。'
          : `已封禁该账号至 ${formatDateTime(result.ban_until)}，目标会话已失效。`
    }
    banSubmitting.value = false
    closeBanDialog(true)
  } catch (reason) {
    banError.value = actionError(
      reason,
      banMode.value === 'unban' ? '解封失败，请稍后重试。' : '封禁失败，请稍后重试。'
    )
  } finally {
    banSubmitting.value = false
  }
}

function activityStatus(activity: AdminUserActivity): string {
  const status = String(activity.mine?.status ?? activity.status ?? '')
  if (status === 'effective') return '已确认'
  if (status === 'refused') return '已拒绝'
  if (status === 'pending') return '待确认'
  return status || '未知'
}

function activityTone(activity: AdminUserActivity): string {
  const status = String(activity.mine?.status ?? activity.status ?? '')
  if (status === 'effective') return 'detail-badge--good'
  if (status === 'refused') return 'detail-badge--bad'
  if (status === 'pending') return 'detail-badge--warn'
  return 'detail-badge--info'
}

function activityMeta(activity: AdminUserActivity): string {
  const parts: string[] = []
  const date = formatDate(activity.date)
  if (date) parts.push(date)
  const mode = activity.mine?.mode || activity.type || ''
  const type = mode ? ACTIVITY_TYPES[mode] ?? mode : ''
  if (type) parts.push(type)
  const duration = Number(activity.mine?.duration)
  if (Number.isFinite(duration)) parts.push(`${formatHours(duration)}h`)
  const place = (activity.place ?? '').trim()
  if (place) parts.push(place)
  return parts.join(' · ') || '暂无活动时间记录'
}

onMounted(() => {
  void session.ensureProfile()
  if (canEditClass) void loadClassOptions()
  start()
})

watch(userId, (next, previous) => {
  if (previous && next !== previous) start()
})
</script>

<template>
  <AdminWriteVerification ref="adminVerification" />
  <dialog
    ref="banDialog"
    class="ban-dialog"
    aria-labelledby="ban-dialog-title"
    aria-modal="true"
    @cancel.prevent="closeBanDialog()"
    @click="onBanBackdrop"
  >
    <form class="ban-dialog__form" @submit.prevent="submitBan">
      <header class="ban-dialog__head">
        <h2 id="ban-dialog-title" class="ban-dialog__title">
          {{ banMode === 'unban' ? '解封账号' : '封禁账号' }}
        </h2>
        <p v-if="profile" class="ban-dialog__target">
          {{ profile.name || '未命名用户' }} · <span class="detail-mono">{{ profile.id }}</span>
        </p>
      </header>

      <template v-if="banMode === 'ban'">
        <div class="edit-field">
          <label class="field__label" for="ban-reason">封禁原因（必填）</label>
          <textarea
            id="ban-reason"
            v-model="banReason"
            class="input ban-dialog__reason"
            rows="3"
            :maxlength="BAN_REASON_MAX"
            placeholder="例如：违规使用平板、转借账号等"
            :disabled="banSubmitting"
            autofocus
          ></textarea>
          <p
            class="ban-dialog__counter"
            :class="{ 'is-over': banReason.length > BAN_REASON_MAX }"
          >
            {{ banReason.length }} / {{ BAN_REASON_MAX }}
          </p>
          <p v-if="banReasonInvalid" class="field__hint field__hint--warn">
            请填写 1–{{ BAN_REASON_MAX }} 字的封禁原因。
          </p>
        </div>

        <fieldset class="ban-dialog__durations">
          <legend class="field__label">封禁时长</legend>
          <div class="ban-duration-options">
            <label
              v-for="days in BAN_DURATION_PRESETS"
              :key="days"
              class="ban-duration-option"
              :class="{ 'is-active': banDurationChoice === String(days) }"
            >
              <input
                v-model="banDurationChoice"
                type="radio"
                :value="String(days)"
                :disabled="banSubmitting"
              />
              <span>{{ days }} 天</span>
            </label>
            <label
              class="ban-duration-option"
              :class="{ 'is-active': banDurationChoice === 'permanent' }"
            >
              <input
                v-model="banDurationChoice"
                type="radio"
                value="permanent"
                :disabled="banSubmitting"
              />
              <span>永久</span>
            </label>
            <label
              class="ban-duration-option"
              :class="{ 'is-active': banDurationChoice === 'custom' }"
            >
              <input
                v-model="banDurationChoice"
                type="radio"
                value="custom"
                :disabled="banSubmitting"
              />
              <span>自定义</span>
            </label>
          </div>

          <div v-if="banDurationChoice === 'custom'" class="ban-dialog__custom">
            <input
              v-model="banCustomDays"
              class="input ban-dialog__days"
              type="number"
              inputmode="numeric"
              min="1"
              :max="BAN_DURATION_MAX"
              step="1"
              aria-label="自定义封禁天数"
              :disabled="banSubmitting"
            />
            <span class="ban-dialog__unit">天</span>
          </div>
          <p v-if="banDurationInvalid" class="field__hint field__hint--warn">
            时长需为 1–{{ BAN_DURATION_MAX }} 的整数天。
          </p>
          <p class="field__hint">
            不选永久时按服务器时间从此刻起算；到期后自动解封。
          </p>
        </fieldset>

        <p class="ban-dialog__note">
          封禁会立即让目标用户的当前登录失效，并阻止其重新登录，直到到期或解封。
        </p>
      </template>

      <template v-else>
        <p class="ban-dialog__note">
          解封后目标用户可立即重新登录；此操作只清除当前封禁状态，不改动历史记录。
        </p>
      </template>

      <BubbleFeedback v-if="banError" class="glass-feedback--error" role="alert">
        {{ banError }}
      </BubbleFeedback>

      <div class="ban-dialog__actions">
        <button
          type="button"
          class="detail-button"
          :disabled="banSubmitting"
          @click="closeBanDialog()"
        >
          取消
        </button>
        <button
          type="submit"
          class="danger-button danger-button--solid"
          :disabled="!banCanSubmit"
        >
          <span v-if="banSubmitting" class="spinner" aria-hidden="true"></span>
          {{ banSubmitting ? '提交中…' : banMode === 'unban' ? '确认解封' : '确认封禁' }}
        </button>
      </div>
    </form>
  </dialog>
  <div class="user-page">
    <div class="user-page__container">
      <header class="user-page__header">
        <button type="button" class="user-page__back" aria-label="返回用户列表" @click="backToList">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path
              d="M12 4L6 10L12 16"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <div class="user-page__heading">
          <p class="user-page__eyebrow">平台管理 · 用户资料</p>
          <h1 class="user-page__title">
            {{ profile?.name || '用户资料' }}
          </h1>
          <p v-if="profile" class="user-page__subtitle">
            <span class="detail-mono">{{ profile.id }}</span>
            <span>{{ className || profile.class_id || '未分班' }}</span>
          </p>
        </div>
        <div class="user-page__badges">
          <span
            v-if="profile"
            class="detail-badge"
            :class="profile.banned ? 'detail-badge--bad' : 'detail-badge--good'"
          >
            {{ profile.banned ? '已封禁' : '正常' }}
          </span>
          <button
            v-if="canModerateBan"
            type="button"
            class="danger-button"
            @click="openBanDialog"
          >
            {{ banActionLabel }}
          </button>
          <span v-if="isSecretaryUser" class="detail-badge detail-badge--info">团支书视图</span>
        </div>
      </header>

      <div v-if="profileLoading" class="detail-state" role="status">
        <span class="detail-spinner" aria-hidden="true"></span>
        正在读取用户资料…
      </div>

      <div v-else-if="profileError" class="detail-state detail-state--error" role="alert">
        <BubbleFeedback class="glass-feedback--error">{{ profileError }}</BubbleFeedback>
        <button type="button" class="detail-button" @click="retryProfile">重试</button>
      </div>

      <div v-else-if="notFound || !profile" class="detail-state" role="alert">
        用户不存在或已被删除。
        <button type="button" class="detail-button" @click="backToList">返回用户列表</button>
      </div>

      <template v-else>
        <div v-if="isSecretaryUser && !sameClass" class="detail-inline-error" role="alert">
          <span>你只能查看本班同学；该用户不在你负责的班级。</span>
          <button type="button" class="detail-button" @click="backToList">返回本班列表</button>
        </div>

        <div class="user-page__grid">
          <section class="user-panel" aria-labelledby="user-profile-title">
            <div class="user-panel__head">
              <h2 id="user-profile-title" class="user-panel__title">基本信息</h2>
              <span class="detail-badge detail-badge--info">{{ displayRoles.join(' · ') }}</span>
            </div>

            <dl class="detail-grid">
              <div>
                <dt>姓名</dt>
                <dd>{{ profile.name || '未命名用户' }}</dd>
              </div>
              <div>
                <dt>学号</dt>
                <dd class="detail-mono">{{ profile.id ?? '—' }}</dd>
              </div>
              <div>
                <dt>班级</dt>
                <dd>{{ className || profile.class_id || '未分班' }}</dd>
              </div>
              <div>
                <dt>角色</dt>
                <dd class="detail-chips">
                  <span v-for="role in displayRoles" :key="role" class="detail-chip">
                    {{ role }}
                  </span>
                </dd>
              </div>
            </dl>

            <p v-if="profile.banned" class="detail-ban" role="note">
              {{ banSummary }}
              <template v-if="profile.ban_reason"> · 原因：{{ profile.ban_reason }}</template>
            </p>
            <BubbleFeedback
              v-if="banNotice"
              class="inline-note glass-feedback--success"
              role="status"
            >
              {{ banNotice }}
            </BubbleFeedback>
          </section>

          <section class="user-panel" aria-labelledby="user-hours-title">
            <div class="user-panel__head">
              <h2 id="user-hours-title" class="user-panel__title">个人时长</h2>
              <span v-if="timeLoading" class="user-panel__hint" role="status">读取中…</span>
            </div>

            <div v-if="timeError" class="detail-inline-error" role="alert">
              <span>{{ timeError }}</span>
              <button type="button" class="detail-button" :disabled="timeLoading" @click="retryTime">
                重试
              </button>
            </div>

            <template v-else>
              <dl class="detail-grid detail-grid--hours" :aria-busy="timeLoading">
                <div v-for="row in hourRows" :key="row.label">
                  <dt>{{ row.label }}</dt>
                  <dd>
                    {{ timeLoading ? '—' : formatHours(row.value) }}<span class="detail-unit">h</span>
                  </dd>
                </div>
                <div class="detail-total">
                  <dt>总时长</dt>
                  <dd>
                    {{ timeLoading ? '—' : formatHours(totalHours) }}<span class="detail-unit">h</span>
                  </dd>
                </div>
              </dl>
              <p class="detail-note">
                仅统计活动与成员记录均为已确认（effective）的时长，按接口原始口径展示。
              </p>
            </template>
          </section>
        </div>

        <section class="user-panel" aria-labelledby="user-edit-title">
          <div class="user-panel__head">
            <h2 id="user-edit-title" class="user-panel__title">资料管理</h2>
            <span class="detail-badge" :class="canEditClass ? 'detail-badge--info' : ''">
              {{ canEditClass ? '可编辑班级' : isAdminUser ? '管理员可编辑' : '只读' }}
            </span>
          </div>

          <template v-if="canEditClass">
            <p class="user-panel__note">
              保存时只提交发生变化的字段。角色或学号一旦变更，目标用户当前登录会立即失效。
            </p>

            <BubbleFeedback v-if="saveNotice" class="inline-note glass-feedback--success" role="status">
              {{ saveNotice }}
            </BubbleFeedback>
            <BubbleFeedback v-if="saveError" class="inline-note glass-feedback--error" role="alert">
              {{ saveError }}
            </BubbleFeedback>

            <div class="edit-grid">
              <fieldset v-if="isAdminUser" class="edit-field edit-fieldset">
                <legend class="field__label">角色</legend>
                <p v-if="roleEditingBlockedReason" class="field__hint">
                  {{ roleEditingBlockedReason }}
                </p>
                <div class="role-options">
                  <label
                    v-for="role in ADMIN_EDITABLE_ROLES"
                    :key="role"
                    class="role-option"
                    :class="{ 'is-disabled': !rolesEditable }"
                  >
                    <input
                      v-model="roleDraft"
                      type="checkbox"
                      :value="role"
                      :disabled="!rolesEditable || saving"
                    />
                    <span>{{ ROLE_LABELS[role] ?? role }}</span>
                    <span class="role-option__code">{{ role }}</span>
                  </label>
                </div>
                <p v-if="roleChanged" class="field__hint field__hint--warn">
                  角色变更会覆盖现有角色，并使目标用户重新登录。
                </p>
              </fieldset>

              <div v-if="isAdminUser" class="edit-field">
                <label class="field__label" for="user-student-id">学号（登录名）</label>
                <input
                  id="user-student-id"
                  v-model="studentIdDraft"
                  class="input"
                  type="text"
                  maxlength="32"
                  autocomplete="off"
                  :disabled="!studentIdEditable || saving"
                />
                <p v-if="studentIdBlockedReason" class="field__hint">
                  {{ studentIdBlockedReason }}
                </p>
                <p v-else class="field__hint">
                  非空、≤32 字符且不含空白；保存后旧登录凭证立即失效。
                </p>
              </div>

              <div class="edit-field">
                <label class="field__label" for="user-class">班级</label>
                <select
                  id="user-class"
                  v-model="classDraft"
                  class="input"
                  :disabled="saving || classOptionsLoading"
                >
                  <option value="">未分班（清空班级）</option>
                  <option v-for="group in classChoices" :key="group._id" :value="group._id">
                    {{ group.name }}
                  </option>
                </select>
                <p v-if="classOptionsLoading" class="field__hint">正在读取班级列表…</p>
                <p v-else-if="classOptionsError" class="field__hint field__hint--warn">
                  {{ classOptionsError }}
                  <button type="button" class="text-button" @click="loadClassOptions">重试</button>
                </p>
                <p v-else class="field__hint">
                  清空时调用 DELETE 移出原班级，选择新班级则直接设置。
                </p>
              </div>
            </div>

            <div class="form__actions">
              <button
                type="button"
                class="primary-button"
                :disabled="saving || !hasChanges"
                @click="saveChanges"
              >
                <span v-if="saving" class="spinner" aria-hidden="true"></span>
                {{ saving ? '保存中…' : '保存改动' }}
              </button>
            </div>
          </template>

          <p v-else class="user-panel__note">
            当前角色只能查看资料。团支书仅可为本班同学签发一次性临时密码。
          </p>
        </section>

        <section class="user-panel" aria-labelledby="user-temp-title">
          <div class="user-panel__head">
            <h2 id="user-temp-title" class="user-panel__title">一次性临时密码</h2>
            <span class="detail-badge detail-badge--warn">仅显示一次</span>
          </div>

          <p class="user-panel__note">
            供目标用户在非主设备上完成登录验证。签发前需要验证你自己的账号密码；
            密码不会发送给临时密码接口，只提交一次性的验证令牌。
          </p>

          <BubbleFeedback
            v-if="tempPasswordError"
            class="inline-note glass-feedback--error"
            role="alert"
          >
            {{ tempPasswordError }}
          </BubbleFeedback>

          <div v-if="canIssueTempPassword" class="temp-password">
            <button
              type="button"
              class="primary-button"
              :disabled="tempPasswordBusy"
              @click="issueTempPassword"
            >
              <span v-if="tempPasswordBusy" class="spinner" aria-hidden="true"></span>
              {{ tempPasswordBusy ? '签发中…' : '验证密码并签发临时密码' }}
            </button>

            <div v-if="tempPassword" class="temp-password__result" role="status">
              <p class="temp-password__label">临时密码（关闭或刷新后不再显示）</p>
              <div class="temp-password__code-row">
                <code class="temp-password__code">{{ tempPassword.code }}</code>
                <button type="button" class="detail-button" @click="copyTempPassword">
                  {{ tempPasswordCopied ? '已复制' : '复制' }}
                </button>
              </div>
              <p class="temp-password__expiry">
                有效期至 {{ formatDateTime(tempPassword.expires_at) }}。
              </p>
              <p class="temp-password__warning">
                请立即告知本人并提醒尽快使用；平台不会再次展示这串密码。
              </p>
            </div>
          </div>

          <p v-else class="user-panel__note">
            {{ isSecretaryUser ? '只能为本班同学签发临时密码。' : '当前角色不能签发临时密码。' }}
          </p>
        </section>

        <section class="user-panel" aria-labelledby="user-activities-title">
          <div class="user-panel__head">
            <h2 id="user-activities-title" class="user-panel__title">活动记录</h2>
            <span v-if="activitiesLoaded" class="detail-badge detail-badge--info">
              共 {{ activitiesTotal }} 条
            </span>
          </div>

          <div v-if="activitiesLoading && !activitiesLoaded" class="detail-state" role="status">
            <span class="detail-spinner" aria-hidden="true"></span>
            正在读取活动记录…
          </div>

          <div
            v-else-if="activitiesError && !activitiesLoaded"
            class="detail-state detail-state--error"
            role="alert"
          >
            <BubbleFeedback class="glass-feedback--error">{{ activitiesError }}</BubbleFeedback>
            <button type="button" class="detail-button" @click="goActivities(0)">重试</button>
          </div>

          <template v-else>
            <BubbleFeedback
              v-if="activitiesError"
              class="inline-note glass-feedback--error"
              role="alert"
            >
              {{ activitiesError }}
            </BubbleFeedback>

            <p v-if="!activities.length" class="detail-empty">暂无活动记录。</p>
            <ul v-else class="detail-activity-list" :class="{ 'is-loading': activitiesLoading }">
              <li
                v-for="activity in activities"
                :key="activity.mine?._id || activity._id"
                class="detail-activity"
              >
                <div class="detail-activity__main">
                  <span class="detail-activity__name">{{ activity.name || '未命名活动' }}</span>
                  <span class="detail-activity__meta">{{ activityMeta(activity) }}</span>
                </div>
                <span class="detail-badge" :class="activityTone(activity)">
                  {{ activityStatus(activity) }}
                </span>
              </li>
            </ul>

            <nav
              v-if="activitiesTotal > ACTIVITY_PAGE_SIZE"
              class="detail-pager"
              aria-label="活动记录分页"
            >
              <span class="detail-pager__info" aria-live="polite">
                第 {{ activitiesPage }} / {{ activityPages }} 页 · 每页
                {{ ACTIVITY_PAGE_SIZE }} 条
              </span>
              <div class="detail-pager__actions">
                <button
                  type="button"
                  class="detail-button"
                  :disabled="activitiesLoading || activitiesPage <= 1"
                  @click="goActivities(-1)"
                >
                  上一页
                </button>
                <button
                  type="button"
                  class="detail-button"
                  :disabled="activitiesLoading || activitiesPage >= activityPages"
                  @click="goActivities(1)"
                >
                  下一页
                </button>
              </div>
            </nav>
          </template>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.user-page {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.user-page__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.user-page__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.user-page__back {
  width: 44px;
  height: 44px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.48) 100%
  );
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    0 2px 8px rgba(18, 48, 82, 0.08);
  color: var(--color-text-primary);
}

.user-page__back:hover {
  color: var(--color-brand);
}

.user-page__heading {
  flex: 1;
  min-width: 0;
}

.user-page__eyebrow {
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
}

.user-page__title {
  margin-top: 2px;
  font-size: var(--font-size-title-3);
  line-height: var(--line-height-snug);
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.user-page__subtitle {
  display: flex;
  flex-wrap: wrap;
  gap: 4px var(--space-2);
  margin-top: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.user-page__badges {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.user-page__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-4);
}

.user-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  border: 1px solid var(--glass-border);
  background: var(--surface-matte);
  box-shadow: var(--glass-shadow);
}

.user-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.user-panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
}

.user-panel__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.user-panel__note {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.detail-state {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-4) 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.detail-state--error {
  flex-direction: column;
  align-items: flex-start;
}

.detail-button {
  min-height: 44px;
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.72);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-caption);
  font-weight: 600;
  cursor: pointer;
}

.detail-button:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
}

.detail-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
  border: 1px solid rgba(0, 113, 227, 0.22);
  background: linear-gradient(135deg, #409cff 0%, #0071e3 100%);
  color: #ffffff;
  font-family: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 3px 12px rgba(0, 113, 227, 0.24);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.primary-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 18px rgba(0, 113, 227, 0.3);
}

.primary-button:active:not(:disabled) {
  transform: scale(0.97);
}

.primary-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
}

.primary-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.danger-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 40px;
  padding: 0 var(--space-4);
  border: 1px solid rgba(215, 0, 21, 0.3);
  border-radius: var(--radius-pill);
  background: rgba(255, 59, 48, 0.1);
  color: #b3261e;
  font-family: inherit;
  font-size: var(--font-size-caption);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth),
    color var(--duration-fast) var(--ease-smooth);
}

.danger-button:hover:not(:disabled) {
  background: rgba(255, 59, 48, 0.16);
  border-color: rgba(215, 0, 21, 0.45);
}

.danger-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.danger-button--solid {
  border-color: transparent;
  background: linear-gradient(135deg, #ff5a50 0%, #d70015 100%);
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    0 3px 12px rgba(215, 0, 21, 0.24);
}

.danger-button--solid:hover:not(:disabled) {
  background: linear-gradient(135deg, #ff6a60 0%, #a50011 100%);
  border-color: transparent;
}

.ban-dialog {
  width: min(560px, calc(100% - 32px));
  max-height: min(86vh, 720px);
  margin: auto;
  padding: var(--space-5);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-xl);
  background: var(--color-bg-elevated);
  color: var(--color-text-primary);
  box-shadow: var(--shadow-xl);
  overflow-y: auto;
}

.ban-dialog::backdrop {
  background: rgb(16 27 50 / 42%);
}

.ban-dialog__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ban-dialog__head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ban-dialog__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
}

.ban-dialog__target {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.ban-dialog__reason {
  min-height: 84px;
  resize: vertical;
}

.ban-dialog__counter {
  align-self: flex-end;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.ban-dialog__counter.is-over {
  color: #b3261e;
}

.ban-dialog__durations {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.ban-duration-options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: 6px;
}

.ban-duration-option {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 38px;
  padding: 6px 12px;
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  background: var(--color-bg-sunken);
  font-size: var(--font-size-caption);
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--ease-smooth),
    border-color var(--duration-fast) var(--ease-smooth);
}

.ban-duration-option.is-active {
  border-color: rgba(215, 0, 21, 0.45);
  background: rgba(255, 59, 48, 0.12);
  color: #b3261e;
  font-weight: 600;
}

.ban-duration-option input {
  accent-color: #d70015;
}

.ban-dialog__custom {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.ban-dialog__days {
  width: 132px;
}

.ban-dialog__unit {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.ban-dialog__note {
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--tone-rose-bg);
  color: #b3261e;
  font-size: var(--font-size-caption);
  line-height: var(--line-height-normal);
}

.ban-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.input {
  width: 100%;
  padding: 10px var(--space-3);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.62);
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
  box-shadow: inset 0 1px 2px rgba(18, 48, 82, 0.04);
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.input:focus-visible,
select.input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
  border-color: transparent;
}

.form__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.inline-note {
  margin-top: var(--space-2);
}

.empty-inline {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
}

.spinner {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 2px solid rgba(0, 113, 227, 0.2);
  border-top-color: var(--color-brand);
  animation: spin 700ms linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.detail-spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--color-border-moderate);
  border-top-color: var(--color-brand);
  animation: detail-spin 720ms linear infinite;
}

@keyframes detail-spin {
  to {
    transform: rotate(360deg);
  }
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-2);
  margin: 0;
}

.detail-grid > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.5);
}

.detail-grid dt {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.detail-grid dd {
  margin: 0;
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-grid--hours {
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
}

.detail-total {
  border-color: var(--tone-blue-bg);
  background: var(--tone-blue-bg);
}

.detail-note {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.detail-unit {
  margin-left: 2px;
  font-size: var(--font-size-caption);
  font-weight: 500;
  color: var(--color-text-secondary);
}

.detail-mono {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: var(--font-size-caption);
  font-weight: 500;
}

.detail-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-weight: 500;
}

.detail-chip {
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--tone-blue-bg);
  color: var(--accent-ink);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
}

.detail-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
}

.detail-badge--good {
  background: var(--tone-green-bg);
  color: #1d7a34;
}

.detail-badge--warn {
  background: var(--tone-amber-bg);
  color: #8a5a00;
}

.detail-badge--bad {
  background: rgba(255, 59, 48, 0.12);
  color: #b3261e;
}

.detail-badge--info {
  background: var(--tone-blue-bg);
  color: var(--accent-ink);
}

.detail-ban {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(255, 59, 48, 0.08);
  color: #b3261e;
  font-size: var(--font-size-caption);
  overflow-wrap: anywhere;
}

.detail-inline-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(255, 59, 48, 0.08);
  color: var(--color-text-primary);
  font-size: var(--font-size-caption);
}

.detail-empty {
  padding: var(--space-3) 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.detail-activity-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 0;
  padding: 0;
  transition: opacity var(--duration-fast) var(--ease-smooth);
}

.detail-activity-list.is-loading {
  opacity: 0.55;
}

.detail-activity {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-hairline);
  min-width: 0;
}

.detail-activity:last-child {
  border-bottom: none;
}

.detail-activity__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.detail-activity__name {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.detail-activity__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.detail-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.detail-pager__info {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.detail-pager__actions {
  display: flex;
  gap: var(--space-2);
}

.edit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-4);
}

.edit-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.edit-fieldset {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.field__label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.field__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.field__hint--warn {
  color: #8a5a00;
}

.role-options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.role-option {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.56);
  font-size: var(--font-size-caption);
  cursor: pointer;
}

.role-option.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.role-option input {
  accent-color: var(--color-brand);
}

.role-option__code {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 10px;
  color: var(--color-text-tertiary);
}

.text-button {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-brand);
  font: inherit;
  font-size: var(--font-size-caption);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.temp-password {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.temp-password__result {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border: 1px solid var(--tone-amber-bg);
  border-radius: var(--radius-lg);
  background: rgba(255, 159, 10, 0.06);
}

.temp-password__label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: #8a5a00;
}

.temp-password__code-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.temp-password__code {
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid var(--color-border-moderate);
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 24px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--color-text-primary);
  user-select: all;
}

.temp-password__expiry,
.temp-password__warning {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.temp-password__warning {
  color: #8a5a00;
}

@media (max-width: 640px) {
  .user-page {
    padding: var(--space-5) var(--space-4) var(--space-9);
  }

  .user-page__title {
    font-size: 26px;
  }

  .user-panel {
    padding: var(--space-4);
  }

  .ban-dialog {
    padding: var(--space-4);
  }
}

@media (prefers-reduced-motion: reduce) {
  .detail-spinner {
    animation: none;
  }

  .detail-activity-list {
    transition: none;
  }
}
</style>
