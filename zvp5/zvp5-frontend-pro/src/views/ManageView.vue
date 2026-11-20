<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AnimatedTabs from '@/components/AnimatedTabs.vue'
import type { AnimatedTabItem } from '@/components/AnimatedTabs.vue'
import { ApiError, readableError } from '@/api/http'
import {
  createNotification,
  fetchActivityCount,
  fetchAdminNotifications,
  fetchAttachments,
  fetchBanRecords,
  fetchBanRules,
  fetchGroups,
  fetchGroupCount,
  fetchLogs,
  fetchRolePolicies,
  fetchSystemSettings,
  fetchUserCount,
  fetchUsers,
  saveSystemSettings,
  updateNotificationActive,
  type AdminAttachment,
  type AdminBanRecord,
  type AdminBanRule,
  type AdminGroup,
  type AdminLog,
  type AdminNotification,
  type AdminRolePolicies,
  type AdminRolePolicy,
  type AdminSettingItem,
  type AdminSettings,
  type AdminUser,
  type SettingUpdateItem
} from '@/api/admin-manage'
import { renderMarkdown } from '@/utils/markdown'

const router = useRouter()

const tabs: AnimatedTabItem[] = [
  { value: 'overview', label: '概览', hint: '平台实时计数与近期动态' },
  { value: 'users', label: '用户', hint: '只读用户目录' },
  { value: 'groups', label: '班级', hint: '只读班级与权限组' },
  { value: 'notifications', label: '通知', hint: '发布与启停通知' },
  { value: 'roles', label: '角色', hint: '只读角色能力矩阵' },
  { value: 'logs', label: '日志', hint: '只读审计日志' },
  { value: 'settings', label: '设置', hint: '在线系统设置' },
  { value: 'ban', label: '封禁', hint: '只读规则与记录' },
  { value: 'attachments', label: '附件', hint: '只读附件清单' }
]

const activeTab = ref('overview')
const currentTabLabel = computed(
  () => tabs.find((item) => item.value === activeTab.value)?.label ?? ''
)

function goBack() {
  router.back()
}

/* -------------------------------------------------------------------------- */
/* Formatting helpers                                                          */
/* -------------------------------------------------------------------------- */

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatDateTime(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`
}

function formatUnixSeconds(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return formatDateTime(new Date(value * 1000))
}

function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN')
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—'
  if (value < 1024) return `${Math.round(value)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let amount = value
  let unit = ''
  for (const next of units) {
    amount /= 1024
    unit = next
    if (amount < 1024) break
  }
  return `${amount.toFixed(amount >= 100 ? 0 : 1)} ${unit}`
}

function shortOid(value: string | null | undefined): string {
  if (!value) return '—'
  return value.length > 10 ? `…${value.slice(-8)}` : value
}

function tabError(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) return '登录状态已失效，请重新登录。'
    if (reason.status === 403) return '当前账号没有访问该分区的权限。'
  }
  return readableError(reason, fallback)
}

function actionError(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError && reason.status === 401) {
    return '登录状态已失效，请重新登录。'
  }
  return readableError(reason, fallback)
}

function totalPages(total: number, size: number): number {
  if (!Number.isFinite(total) || total <= 0 || size <= 0) return 1
  return Math.max(1, Math.ceil(total / size))
}

interface Settled<T> {
  value?: T
  error?: string
}

async function settle<T>(promise: Promise<T>): Promise<Settled<T>> {
  try {
    return { value: await promise }
  } catch (reason) {
    return { error: readableError(reason, '读取失败') }
  }
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */

const overviewLoading = ref(false)
const overviewError = ref('')
const overviewLoaded = ref(false)
const overviewUsers = ref<number | null>(null)
const overviewGroups = ref<number | null>(null)
const overviewActivities = ref<number | null>(null)
const overviewNotifications = ref<number | null>(null)
const overviewRecentNotifications = ref<AdminNotification[]>([])
const overviewRecentLogs = ref<AdminLog[]>([])

async function loadOverview() {
  if (overviewLoading.value) return
  overviewLoading.value = true
  overviewError.value = ''
  const [users, groups, activities, notifications, logs] = await Promise.all([
    settle(fetchUserCount()),
    settle(fetchGroupCount()),
    settle(fetchActivityCount()),
    settle(fetchAdminNotifications()),
    settle(fetchLogs(1, 5, ''))
  ])

  overviewUsers.value = users.value ?? null
  overviewGroups.value = groups.value ?? null
  overviewActivities.value = activities.value ?? null
  overviewNotifications.value = notifications.value?.notifications?.length ?? null
  overviewRecentNotifications.value = (notifications.value?.notifications ?? []).slice(0, 4)
  overviewRecentLogs.value = (logs.value?.data ?? []).slice(0, 5)

  const failures = [users.error, groups.error, activities.error, notifications.error, logs.error]
    .filter((message): message is string => typeof message === 'string' && message.length > 0)
    .filter((message, index, list) => list.indexOf(message) === index)

  overviewError.value = failures.length
    ? `部分数据读取失败：${failures.join('；')}`
    : ''
  overviewLoaded.value = true
  overviewLoading.value = false
}

/* -------------------------------------------------------------------------- */
/* Users (read-only)                                                           */
/* -------------------------------------------------------------------------- */

const USER_PAGE_SIZE = 10
const users = ref<AdminUser[]>([])
const usersTotal = ref(0)
const usersPage = ref(1)
const usersQuery = ref('')
const usersQueryDraft = ref('')
const usersRole = ref('')
const usersLoading = ref(false)
const usersError = ref('')
const usersLoaded = ref(false)
let usersSeq = 0

const usersPages = computed(() => totalPages(usersTotal.value, USER_PAGE_SIZE))

async function loadUsers() {
  const seq = ++usersSeq
  usersLoading.value = true
  usersError.value = ''
  try {
    const data = await fetchUsers(
      usersPage.value,
      USER_PAGE_SIZE,
      usersQuery.value,
      usersRole.value
    )
    if (seq !== usersSeq) return
    users.value = Array.isArray(data.data) ? data.data : []
    usersTotal.value = Number.isFinite(Number(data.total)) ? Number(data.total) : 0
    usersLoaded.value = true
  } catch (reason) {
    if (seq !== usersSeq) return
    usersError.value = tabError(reason, '用户列表读取失败。')
  } finally {
    if (seq === usersSeq) usersLoading.value = false
  }
}

function searchUsers() {
  usersQuery.value = usersQueryDraft.value.trim()
  usersPage.value = 1
  void loadUsers()
}

function changeUserRole() {
  usersPage.value = 1
  void loadUsers()
}

function usersPrev() {
  if (usersPage.value <= 1 || usersLoading.value) return
  usersPage.value -= 1
  void loadUsers()
}

function usersNext() {
  if (usersPage.value >= usersPages.value || usersLoading.value) return
  usersPage.value += 1
  void loadUsers()
}

function userRoleText(user: AdminUser): string {
  return user.roles?.length ? user.roles.join(' · ') : '无角色'
}

function userClassText(user: AdminUser): string {
  return user.class_name || user.class_id || '未分班'
}

/* -------------------------------------------------------------------------- */
/* Groups (read-only)                                                          */
/* -------------------------------------------------------------------------- */

const GROUP_PAGE_SIZE = 10
const groups = ref<AdminGroup[]>([])
const groupsTotal = ref(0)
const groupsPage = ref(1)
const groupsQuery = ref('')
const groupsQueryDraft = ref('')
const groupsLoading = ref(false)
const groupsError = ref('')
const groupsLoaded = ref(false)
let groupsSeq = 0

const groupsPages = computed(() => totalPages(groupsTotal.value, GROUP_PAGE_SIZE))

async function loadGroups() {
  const seq = ++groupsSeq
  groupsLoading.value = true
  groupsError.value = ''
  try {
    const data = await fetchGroups(groupsPage.value, GROUP_PAGE_SIZE, groupsQuery.value)
    if (seq !== groupsSeq) return
    groups.value = Array.isArray(data.data) ? data.data : []
    groupsTotal.value = Number.isFinite(Number(data.total)) ? Number(data.total) : 0
    groupsLoaded.value = true
  } catch (reason) {
    if (seq !== groupsSeq) return
    groupsError.value = tabError(reason, '班级列表读取失败。')
  } finally {
    if (seq === groupsSeq) groupsLoading.value = false
  }
}

function searchGroups() {
  groupsQuery.value = groupsQueryDraft.value.trim()
  groupsPage.value = 1
  void loadGroups()
}

function groupsPrev() {
  if (groupsPage.value <= 1 || groupsLoading.value) return
  groupsPage.value -= 1
  void loadGroups()
}

function groupsNext() {
  if (groupsPage.value >= groupsPages.value || groupsLoading.value) return
  groupsPage.value += 1
  void loadGroups()
}

function groupTypeText(group: AdminGroup): string {
  if (group.type === 'permission') return '权限组'
  if (group.type === 'club') return '社团'
  if (group.type === 'group') return '分组'
  return '班级'
}

/* -------------------------------------------------------------------------- */
/* Notifications                                                               */
/* -------------------------------------------------------------------------- */

const notifications = ref<AdminNotification[]>([])
const notificationsVersion = ref(0)
const notificationsLoading = ref(false)
const notificationsError = ref('')
const notificationsLoaded = ref(false)
const notificationExpandedId = ref('')
const notificationBusyId = ref('')
const notificationActionNotice = ref('')
const notificationActionError = ref('')

const notificationForm = reactive({ title: '', content: '' })
const notificationFormErrors = reactive({ title: '', content: '' })
const notificationCreating = ref(false)
const notificationCreateError = ref('')
const notificationCreateNotice = ref('')
const notificationShowPreview = ref(false)

async function loadNotifications() {
  if (notificationsLoading.value) return
  notificationsLoading.value = true
  notificationsError.value = ''
  try {
    const data = await fetchAdminNotifications()
    notifications.value = Array.isArray(data.notifications) ? data.notifications : []
    notificationsVersion.value = Number(data.version) || 0
    notificationsLoaded.value = true
    if (
      notificationExpandedId.value &&
      !notifications.value.some((item) => item.id === notificationExpandedId.value)
    ) {
      notificationExpandedId.value = ''
    }
  } catch (reason) {
    notificationsError.value = tabError(reason, '通知列表读取失败。')
  } finally {
    notificationsLoading.value = false
  }
}

function toggleNotificationContent(id: string) {
  notificationExpandedId.value = notificationExpandedId.value === id ? '' : id
}

function notificationStatusText(notice: AdminNotification): string {
  return notice.active ? '已启用' : '已停用'
}

function notificationHtml(notice: AdminNotification): string {
  return renderMarkdown(notice.content ?? '')
}

async function toggleNotificationActive(notice: AdminNotification) {
  if (notificationBusyId.value) return
  const target = !notice.active
  notificationBusyId.value = notice.id
  notificationActionNotice.value = ''
  notificationActionError.value = ''
  try {
    const result = await updateNotificationActive(notice.id, target)
    const updated = result.notification
    if (updated) {
      const index = notifications.value.findIndex((item) => item.id === notice.id)
      if (index >= 0) notifications.value[index] = updated
    } else {
      notice.active = target
    }
    notificationActionNotice.value = target ? '通知已启用。' : '通知已停用。'
  } catch (reason) {
    notificationActionError.value = actionError(reason, '通知状态更新失败。')
  } finally {
    notificationBusyId.value = ''
  }
}

function validateNotification(): boolean {
  notificationFormErrors.title = ''
  notificationFormErrors.content = ''
  const title = notificationForm.title.trim()
  const content = notificationForm.content.trim()
  if (!title) {
    notificationFormErrors.title = '请填写通知标题。'
  } else if (title.length > 200) {
    notificationFormErrors.title = '标题不能超过 200 个字符。'
  }
  if (!content) {
    notificationFormErrors.content = '请填写通知正文。'
  } else if (content.length > 4000) {
    notificationFormErrors.content = '正文不能超过 4000 个字符。'
  }
  return !notificationFormErrors.title && !notificationFormErrors.content
}

async function submitNotification() {
  if (notificationCreating.value) return
  notificationCreateError.value = ''
  notificationCreateNotice.value = ''
  if (!validateNotification()) return

  notificationCreating.value = true
  try {
    const result = await createNotification(
      notificationForm.title.trim(),
      notificationForm.content.trim()
    )
    if (result.notification) {
      notifications.value = [result.notification, ...notifications.value]
    }
    notificationsVersion.value = Number(result.version) || notificationsVersion.value
    notificationForm.title = ''
    notificationForm.content = ''
    notificationFormErrors.title = ''
    notificationFormErrors.content = ''
    notificationShowPreview.value = false
    notificationCreateNotice.value = '通知已创建并立即生效。'
    notificationsLoaded.value = true
  } catch (reason) {
    notificationCreateError.value = actionError(reason, '通知发布失败，请稍后重试。')
  } finally {
    notificationCreating.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Role policies (read-only)                                                   */
/* -------------------------------------------------------------------------- */

const FALLBACK_FEATURE_KEYS = [
  'ai.chat',
  'ai.ocr',
  'ai.agent',
  'ai.schedule',
  'ai.keys',
  'activity.stats',
  'attachment.manage',
  'export.time',
  'import.activity',
  'logs.read',
  'notify.publish',
  'ban.read',
  'quota.manage'
]

const FEATURE_LABELS: Record<string, string> = {
  'ai.chat': 'AI 对话',
  'ai.ocr': 'OCR 识别',
  'ai.agent': 'AI 代理',
  'ai.schedule': 'AI 定时任务',
  'ai.keys': 'AI 凭据管理',
  'activity.stats': '活动统计',
  'attachment.manage': '附件管理',
  'export.time': '导出时长',
  'import.activity': '导入活动',
  'logs.read': '读取日志',
  'notify.publish': '发布通知',
  'ban.read': '查看封禁',
  'quota.manage': '配额管理'
}

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  department: '学生会',
  secretary: '团支书',
  student: '学生',
  auditor: '审计员',
  system: '系统'
}

const ROLE_ORDER = ['admin', 'department', 'secretary', 'student', 'auditor']
const QUOTA_LABELS: Record<string, string> = {
  af: 'AI 额度',
  schedule: '定时任务'
}

const rolePolicies = ref<AdminRolePolicies | null>(null)
const rolesLoading = ref(false)
const rolesError = ref('')
const rolesLoaded = ref(false)

const roleFeatureKeys = computed(() => {
  const fromApi = rolePolicies.value?.feature_keys ?? []
  return fromApi.length ? fromApi : FALLBACK_FEATURE_KEYS
})

const sortedRolePolicies = computed<AdminRolePolicy[]>(() => {
  const list = rolePolicies.value?.roles ?? []
  return [...list].sort((left, right) => {
    const leftIndex = ROLE_ORDER.indexOf(left.role)
    const rightIndex = ROLE_ORDER.indexOf(right.role)
    const leftRank = leftIndex < 0 ? ROLE_ORDER.length : leftIndex
    const rightRank = rightIndex < 0 ? ROLE_ORDER.length : rightIndex
    if (leftRank !== rightRank) return leftRank - rightRank
    return left.role.localeCompare(right.role)
  })
})

async function loadRolePolicies() {
  if (rolesLoading.value) return
  rolesLoading.value = true
  rolesError.value = ''
  try {
    rolePolicies.value = await fetchRolePolicies()
    rolesLoaded.value = true
  } catch (reason) {
    rolesError.value = tabError(reason, '角色策略读取失败。')
  } finally {
    rolesLoading.value = false
  }
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

function featureLabel(key: string): string {
  return FEATURE_LABELS[key] ?? key
}

function featureEnabled(policy: AdminRolePolicy, key: string): boolean {
  return policy.features?.[key] !== false
}

function quotaText(value: number | null | undefined): string {
  if (value === null || value === undefined) return '系统默认'
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toLocaleString('zh-CN')
}

/* -------------------------------------------------------------------------- */
/* Settings                                                                    */
/* -------------------------------------------------------------------------- */

const SETTING_GROUP_ORDER = ['ai', 'ops']
const SETTING_GROUP_LABELS: Record<string, string> = {
  ai: 'AI 网关',
  ops: '运维与配额'
}

interface SettingDraft {
  bool: boolean
  text: string
}

const settings = ref<AdminSettings | null>(null)
const settingsLoading = ref(false)
const settingsError = ref('')
const settingsLoaded = ref(false)
const settingsSaving = ref(false)
const settingsSaveError = ref('')
const settingsSaveNotice = ref('')
const settingDrafts = ref<Record<string, SettingDraft>>({})

const settingGroups = computed(() => {
  const items = settings.value?.items ?? []
  const buckets = new Map<string, AdminSettingItem[]>()
  for (const item of items) {
    const group = item.group || 'other'
    const bucket = buckets.get(group)
    if (bucket) bucket.push(item)
    else buckets.set(group, [item])
  }
  return [...buckets.entries()]
    .sort((left, right) => {
      const leftIndex = SETTING_GROUP_ORDER.indexOf(left[0])
      const rightIndex = SETTING_GROUP_ORDER.indexOf(right[0])
      const leftRank = leftIndex < 0 ? SETTING_GROUP_ORDER.length : leftIndex
      const rightRank = rightIndex < 0 ? SETTING_GROUP_ORDER.length : rightIndex
      if (leftRank !== rightRank) return leftRank - rightRank
      return left[0].localeCompare(right[0])
    })
    .map(([group, groupItems]) => ({
      group,
      label: SETTING_GROUP_LABELS[group] ?? group,
      items: groupItems
    }))
})

const editableSettingCount = computed(() =>
  (settings.value?.items ?? []).filter((item) => isSettingEditable(item)).length
)

const readOnlySettingCount = computed(() =>
  (settings.value?.items ?? []).filter((item) => !isSettingEditable(item)).length
)

const settingChangedCount = computed(() =>
  (settings.value?.items ?? []).filter((item) => isSettingEditable(item) && isSettingDirty(item))
    .length
)

function isSettingEditable(item: AdminSettingItem): boolean {
  return (
    item.type === 'bool' ||
    item.type === 'int' ||
    item.type === 'float' ||
    item.type === 'str'
  )
}

function isSettingDirty(item: AdminSettingItem): boolean {
  const draft = settingDrafts.value[item.key]
  if (!draft) return false
  if (item.type === 'bool') return draft.bool !== (item.value === true)
  if (item.type === 'int' || item.type === 'float') {
    return draft.text.trim() !== String(item.value ?? '').trim()
  }
  if (item.type === 'str') return draft.text !== String(item.value ?? '')
  return false
}

function seedSettingDrafts() {
  const next: Record<string, SettingDraft> = {}
  for (const item of settings.value?.items ?? []) {
    next[item.key] = {
      bool: item.value === true,
      text:
        typeof item.value === 'string'
          ? item.value
          : item.value === null || item.value === undefined
            ? ''
            : String(item.value)
    }
  }
  settingDrafts.value = next
}

function settingValueText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value || '（空字符串）'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function settingTypeText(item: AdminSettingItem): string {
  if (item.type === 'bool') return '开关'
  if (item.type === 'int') return '整数'
  if (item.type === 'float') return '小数'
  if (item.type === 'str') return '文本'
  if (item.type === 'list') return '列表（只读）'
  if (item.type === 'dict') return '字典（只读）'
  return `${item.type}（只读）`
}

function settingRangeText(item: AdminSettingItem): string {
  if (item.type !== 'int' && item.type !== 'float') return ''
  const min = typeof item.min === 'number' ? item.min : null
  const max = typeof item.max === 'number' ? item.max : null
  if (min === null && max === null) return ''
  if (min !== null && max !== null) return `范围 ${min} – ${max}`
  if (min !== null) return `不小于 ${min}`
  return `不大于 ${max}`
}

async function loadSettings() {
  if (settingsLoading.value) return
  settingsLoading.value = true
  settingsError.value = ''
  try {
    settings.value = await fetchSystemSettings()
    seedSettingDrafts()
    settingsLoaded.value = true
  } catch (reason) {
    settings.value = null
    settingDrafts.value = {}
    settingsError.value = tabError(reason, '系统设置读取失败。')
  } finally {
    settingsLoading.value = false
  }
}

async function saveSettings() {
  if (settingsSaving.value || !settings.value) return
  settingsSaveError.value = ''
  settingsSaveNotice.value = ''
  const items: SettingUpdateItem[] = []
  const problems: string[] = []

  for (const item of settings.value.items) {
    if (!isSettingEditable(item) || !isSettingDirty(item)) continue
    const draft = settingDrafts.value[item.key]
    if (!draft) continue

    if (item.type === 'bool') {
      items.push({ key: item.key, value: draft.bool })
      continue
    }

    if (item.type === 'int' || item.type === 'float') {
      const text = draft.text.trim()
      if (!text) {
        problems.push(`${item.label}：请输入数值`)
        continue
      }
      if (item.type === 'int' && !/^-?\d+$/.test(text)) {
        problems.push(`${item.label}：请输入整数`)
        continue
      }
      const value = Number(text)
      if (!Number.isFinite(value)) {
        problems.push(`${item.label}：请输入有效数值`)
        continue
      }
      if (typeof item.min === 'number' && value < item.min) {
        problems.push(`${item.label}：不能小于 ${item.min}`)
        continue
      }
      if (typeof item.max === 'number' && value > item.max) {
        problems.push(`${item.label}：不能大于 ${item.max}`)
        continue
      }
      items.push({ key: item.key, value })
      continue
    }

    if (item.type === 'str') {
      items.push({ key: item.key, value: draft.text })
    }
  }

  if (problems.length) {
    settingsSaveError.value = problems.join('；')
    return
  }
  if (!items.length) {
    settingsSaveNotice.value = '没有需要保存的改动。'
    return
  }

  settingsSaving.value = true
  try {
    await saveSystemSettings(items)
    settingsSaveNotice.value = `已保存 ${items.length} 项设置。`
    await loadSettings()
  } catch (reason) {
    settingsSaveError.value = actionError(reason, '设置保存失败，请稍后重试。')
  } finally {
    settingsSaving.value = false
  }
}

/* -------------------------------------------------------------------------- */
/* Logs (read-only)                                                            */
/* -------------------------------------------------------------------------- */

const LOG_PAGE_SIZE = 10
const logs = ref<AdminLog[]>([])
const logsTotal = ref(0)
const logsPage = ref(1)
const logsQuery = ref('')
const logsQueryDraft = ref('')
const logsLoading = ref(false)
const logsError = ref('')
const logsLoaded = ref(false)
let logsSeq = 0

const logsPages = computed(() => totalPages(logsTotal.value, LOG_PAGE_SIZE))

async function loadLogs() {
  const seq = ++logsSeq
  logsLoading.value = true
  logsError.value = ''
  try {
    const result = await fetchLogs(logsPage.value, LOG_PAGE_SIZE, logsQuery.value)
    if (seq !== logsSeq) return
    logs.value = Array.isArray(result.data) ? result.data : []
    logsTotal.value = Number.isFinite(result.size) ? result.size : 0
    logsLoaded.value = true
  } catch (reason) {
    if (seq !== logsSeq) return
    logsError.value = tabError(reason, '日志列表读取失败。')
  } finally {
    if (seq === logsSeq) logsLoading.value = false
  }
}

function searchLogs() {
  logsQuery.value = logsQueryDraft.value.trim()
  logsPage.value = 1
  void loadLogs()
}

function logsPrev() {
  if (logsPage.value <= 1 || logsLoading.value) return
  logsPage.value -= 1
  void loadLogs()
}

function logsNext() {
  if (logsPage.value >= logsPages.value || logsLoading.value) return
  logsPage.value += 1
  void loadLogs()
}

function logLevelText(level: string | undefined): string {
  if (level === 'warning') return '警告'
  if (level === 'error') return '错误'
  if (level === 'debug') return '调试'
  if (level === 'verbose') return '详细'
  return '普通'
}

function logLevelClass(level: string | undefined): string {
  if (level === 'error') return 'badge--bad'
  if (level === 'warning') return 'badge--warn'
  if (level === 'debug' || level === 'verbose') return 'badge--info'
  return ''
}

function logPerformer(log: AdminLog): string {
  if (log.user_name) return log.user_name
  if (log.user_id) return log.user_id
  if (log.user) return shortOid(log.user)
  return '未登录'
}

/* -------------------------------------------------------------------------- */
/* Ban rules and records (read-only)                                           */
/* -------------------------------------------------------------------------- */

const BAN_PAGE_SIZE = 10
const banRules = ref<AdminBanRule[]>([])
const banRecords = ref<AdminBanRecord[]>([])
const banTotal = ref(0)
const banPage = ref(1)
const banQuery = ref('')
const banQueryDraft = ref('')
const banLoading = ref(false)
const banRecordsLoading = ref(false)
const banLoaded = ref(false)
const banRulesError = ref('')
const banRecordsError = ref('')
let banRecordsSeq = 0

const banPages = computed(() => totalPages(banTotal.value, BAN_PAGE_SIZE))

async function loadBanRules() {
  banRulesError.value = ''
  try {
    const rules = await fetchBanRules()
    banRules.value = Array.isArray(rules) ? rules : []
  } catch (reason) {
    banRules.value = []
    banRulesError.value = tabError(reason, '封禁规则读取失败。')
  }
}

async function loadBanRecords() {
  const seq = ++banRecordsSeq
  banRecordsLoading.value = true
  banRecordsError.value = ''
  try {
    const result = await fetchBanRecords(banPage.value, BAN_PAGE_SIZE, banQuery.value)
    if (seq !== banRecordsSeq) return
    banRecords.value = Array.isArray(result.data) ? result.data : []
    banTotal.value = Number.isFinite(result.size) ? result.size : 0
  } catch (reason) {
    if (seq !== banRecordsSeq) return
    banRecords.value = []
    banTotal.value = 0
    banRecordsError.value = tabError(reason, '封禁记录读取失败。')
  } finally {
    if (seq === banRecordsSeq) banRecordsLoading.value = false
  }
}

async function loadBan() {
  if (banLoading.value) return
  banLoading.value = true
  await Promise.all([loadBanRules(), loadBanRecords()])
  banLoaded.value = true
  banLoading.value = false
}

function searchBanRecords() {
  banQuery.value = banQueryDraft.value.trim()
  banPage.value = 1
  void loadBanRecords()
}

function banPrev() {
  if (banPage.value <= 1 || banRecordsLoading.value) return
  banPage.value -= 1
  void loadBanRecords()
}

function banNext() {
  if (banPage.value >= banPages.value || banRecordsLoading.value) return
  banPage.value += 1
  void loadBanRecords()
}

function banPenaltiesText(rule: AdminBanRule): string {
  if (!rule.penalties?.length) return '未配置梯度'
  return rule.penalties
    .map((value) => (value === null ? '永久' : `${value} 天`))
    .join(' → ')
}

function banTriggerText(rule: AdminBanRule): string {
  return rule.trigger_name || rule.trigger || rule.code
}

function banRecordExpiry(record: AdminBanRecord): string {
  if (record.permanent) return '永久'
  return record.expires_at ? formatDateTime(record.expires_at) : '未知'
}

function banRecordSource(record: AdminBanRecord): string {
  if (record.source === 'rule') {
    return record.rule_code ? `规则 · ${record.rule_code}` : '规则'
  }
  if (record.source === 'admin') return '管理员'
  return '—'
}

/* -------------------------------------------------------------------------- */
/* Attachments (read-only)                                                     */
/* -------------------------------------------------------------------------- */

const ATTACHMENT_PAGE_SIZE = 20
const attachments = ref<AdminAttachment[]>([])
const attachmentsTotal = ref(0)
const attachmentsLoading = ref(false)
const attachmentsError = ref('')
const attachmentsLoaded = ref(false)

async function loadAttachments() {
  if (attachmentsLoading.value) return
  attachmentsLoading.value = true
  attachmentsError.value = ''
  try {
    const result = await fetchAttachments(ATTACHMENT_PAGE_SIZE)
    attachments.value = Array.isArray(result.data) ? result.data : []
    attachmentsTotal.value = Number.isFinite(result.size) ? result.size : 0
    attachmentsLoaded.value = true
  } catch (reason) {
    attachments.value = []
    attachmentsTotal.value = 0
    attachmentsError.value = tabError(reason, '附件列表读取失败。')
  } finally {
    attachmentsLoading.value = false
  }
}

function attachmentStatusClass(status: string | undefined): string {
  if (status === 'ready' || status === 'verified') return 'badge--good'
  if (status === 'processing' || status === 'stored') return 'badge--info'
  if (status === 'rejected' || status === 'quarantined') return 'badge--bad'
  return ''
}

function attachmentStatusText(status: string | undefined): string {
  if (!status) return '未知'
  const labels: Record<string, string> = {
    stored: '已存储',
    processing: '处理中',
    ready: '就绪',
    verified: '已核验',
    rejected: '已拒绝',
    quarantined: '隔离中',
    deleted: '已删除'
  }
  return labels[status] ?? status
}

/* -------------------------------------------------------------------------- */
/* Tab wiring                                                                  */
/* -------------------------------------------------------------------------- */

function ensureTab(tab: string) {
  if (tab === 'overview') {
    if (!overviewLoaded.value && !overviewLoading.value) void loadOverview()
    return
  }
  if (tab === 'users') {
    if (!usersLoaded.value && !usersLoading.value && !usersError.value) void loadUsers()
    return
  }
  if (tab === 'groups') {
    if (!groupsLoaded.value && !groupsLoading.value && !groupsError.value) void loadGroups()
    return
  }
  if (tab === 'notifications') {
    if (!notificationsLoaded.value && !notificationsLoading.value) void loadNotifications()
    return
  }
  if (tab === 'roles') {
    if (!rolesLoaded.value && !rolesLoading.value) void loadRolePolicies()
    return
  }
  if (tab === 'logs') {
    if (!logsLoaded.value && !logsLoading.value && !logsError.value) void loadLogs()
    return
  }
  if (tab === 'settings') {
    if (!settingsLoaded.value && !settingsLoading.value) void loadSettings()
    return
  }
  if (tab === 'ban') {
    if (!banLoaded.value && !banLoading.value) void loadBan()
    return
  }
  if (tab === 'attachments') {
    if (!attachmentsLoaded.value && !attachmentsLoading.value) void loadAttachments()
  }
}

const panelBusy = computed(() => {
  if (activeTab.value === 'overview') return overviewLoading.value
  if (activeTab.value === 'users') return usersLoading.value
  if (activeTab.value === 'groups') return groupsLoading.value
  if (activeTab.value === 'notifications') {
    return notificationsLoading.value || notificationCreating.value
  }
  if (activeTab.value === 'roles') return rolesLoading.value
  if (activeTab.value === 'logs') return logsLoading.value
  if (activeTab.value === 'settings') return settingsLoading.value || settingsSaving.value
  if (activeTab.value === 'ban') return banLoading.value
  if (activeTab.value === 'attachments') return attachmentsLoading.value
  return false
})

watch(activeTab, ensureTab, { immediate: true })
</script>

<template>
  <div class="manage">
    <div class="manage__container">
      <header class="manage__header surface-enter" style="--i: 0">
        <button type="button" class="manage__back" aria-label="返回" @click="goBack">
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
        <div class="manage__heading">
          <h1 class="manage__title">平台管理</h1>
          <p class="manage__subtitle">实时平台数据、公告发布与受控运维视图</p>
        </div>
        <div class="manage__header-status" aria-live="polite">
          <span class="status-dot" aria-hidden="true"></span>
          <span>{{ currentTabLabel }}</span>
        </div>
      </header>

      <div class="manage__tabs surface-enter" style="--i: 1">
        <AnimatedTabs v-model="activeTab" :items="tabs" aria-label="平台管理分区" />
      </div>

      <Transition name="tab-fade" mode="out-in">
        <div
          :key="activeTab"
          class="manage__content"
          role="tabpanel"
          :aria-label="currentTabLabel"
          :aria-busy="panelBusy"
        >
          <!-- 概览 -->
          <template v-if="activeTab === 'overview'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">平台概览</h2>
                  <p class="panel__subtitle">来自用户、班级、活动与通知接口的实时计数</p>
                </div>
                <div class="panel__actions">
                  <span class="badge badge--info">{{ overviewLoaded ? '已同步' : '待加载' }}</span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="overviewLoading"
                    @click="loadOverview"
                  >
                    {{ overviewLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="overviewLoading && !overviewLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取平台概况…
              </div>

              <template v-else>
                <p v-if="overviewError" class="inline-note inline-note--warn" role="alert">
                  {{ overviewError }}
                </p>

                <div class="summary-grid">
                  <div class="summary-card surface-enter" style="--i: 0">
                    <span class="summary-card__label">用户</span>
                    <span class="summary-card__value">{{ formatCount(overviewUsers) }}</span>
                    <span class="summary-card__hint">GET /v2/users · total</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 1">
                    <span class="summary-card__label">班级与权限组</span>
                    <span class="summary-card__value">{{ formatCount(overviewGroups) }}</span>
                    <span class="summary-card__hint">GET /v2/groups · total</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 2">
                    <span class="summary-card__label">活动</span>
                    <span class="summary-card__value">{{ formatCount(overviewActivities) }}</span>
                    <span class="summary-card__hint">GET /v2/activities · total</span>
                  </div>
                  <div class="summary-card surface-enter" style="--i: 3">
                    <span class="summary-card__label">通知</span>
                    <span class="summary-card__value">{{ formatCount(overviewNotifications) }}</span>
                    <span class="summary-card__hint">GET /v3/notifications/admin</span>
                  </div>
                </div>

                <div class="overview-columns">
                  <section class="sub-panel surface-enter" style="--i: 1">
                    <div class="sub-panel__head">
                      <h3 class="sub-panel__title">最近通知</h3>
                      <button
                        type="button"
                        class="text-button"
                        @click="activeTab = 'notifications'"
                      >
                        查看全部
                      </button>
                    </div>
                    <ul v-if="overviewRecentNotifications.length" class="row-list">
                      <li
                        v-for="notice in overviewRecentNotifications"
                        :key="notice.id"
                        class="row row--compact"
                      >
                        <div class="row__main">
                          <span class="row__title">{{ notice.title }}</span>
                          <span class="row__meta">
                            {{ notice.author_name || '未知发布人' }} ·
                            {{ formatDateTime(notice.created_at) }}
                          </span>
                        </div>
                        <span
                          class="badge"
                          :class="notice.active ? 'badge--good' : 'badge--info'"
                        >
                          {{ notice.active ? '启用' : '停用' }}
                        </span>
                      </li>
                    </ul>
                    <p v-else class="empty-inline">暂无通知，或当前账号无权读取管理列表。</p>
                  </section>

                  <section class="sub-panel surface-enter" style="--i: 2">
                    <div class="sub-panel__head">
                      <h3 class="sub-panel__title">最近日志</h3>
                      <button type="button" class="text-button" @click="activeTab = 'logs'">
                        查看全部
                      </button>
                    </div>
                    <ul v-if="overviewRecentLogs.length" class="row-list">
                      <li
                        v-for="log in overviewRecentLogs"
                        :key="log._id"
                        class="row row--compact"
                      >
                        <div class="row__main">
                          <span class="row__title">{{ log.type || '日志' }}</span>
                          <span class="row__meta">
                            {{ logPerformer(log) }} · {{ formatUnixSeconds(log.timestamp) }}
                          </span>
                        </div>
                        <span class="badge" :class="logLevelClass(log.level)">
                          {{ logLevelText(log.level) }}
                        </span>
                      </li>
                    </ul>
                    <p v-else class="empty-inline">暂无日志，或当前账号无权读取审计日志。</p>
                  </section>
                </div>
              </template>
            </section>
          </template>

          <!-- 用户 -->
          <template v-else-if="activeTab === 'users'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">用户目录</h2>
                  <p class="panel__subtitle">只读 · 姓名、学号、角色、班级与封禁状态</p>
                </div>
                <div class="panel__actions">
                  <span class="badge badge--info">共 {{ formatCount(usersTotal) }} 人</span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="usersLoading"
                    @click="loadUsers"
                  >
                    {{ usersLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <form class="toolbar" role="search" @submit.prevent="searchUsers">
                <label class="sr-only" for="users-search">搜索用户</label>
                <input
                  id="users-search"
                  v-model="usersQueryDraft"
                  class="input toolbar__search"
                  type="search"
                  placeholder="搜索姓名或学号"
                  autocomplete="off"
                />
                <label class="sr-only" for="users-role">按角色筛选</label>
                <select
                  id="users-role"
                  v-model="usersRole"
                  class="input toolbar__select"
                  @change="changeUserRole"
                >
                  <option value="">全部角色</option>
                  <option value="admin">管理员</option>
                  <option value="department">学生会</option>
                  <option value="secretary">团支书</option>
                  <option value="student">学生</option>
                  <option value="auditor">审计员</option>
                </select>
                <button type="submit" class="primary-button" :disabled="usersLoading">搜索</button>
              </form>

              <div v-if="usersLoading && !usersLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取用户列表…
              </div>

              <div v-else-if="usersError && !usersLoaded" class="state-block state-block--error" role="alert">
                <span>{{ usersError }}</span>
                <button type="button" class="ghost-button" @click="loadUsers">重试</button>
              </div>

              <template v-else>
                <p v-if="usersError" class="inline-note inline-note--error" role="alert">
                  {{ usersError }}
                </p>
                <p v-if="!users.length" class="empty-inline">没有匹配的用户。</p>
                <ul v-else class="row-list" :class="{ 'is-loading': usersLoading }">
                  <li v-for="user in users" :key="user._id" class="row">
                    <div class="row__main">
                      <span class="row__title">
                        {{ user.name || '未命名用户' }}
                        <span class="row__id">{{ user.id }}</span>
                      </span>
                      <span class="row__meta">
                        {{ userRoleText(user) }} · {{ userClassText(user) }}
                      </span>
                    </div>
                    <span class="badge" :class="user.banned ? 'badge--bad' : 'badge--good'">
                      {{ user.banned ? '已封禁' : '正常' }}
                    </span>
                  </li>
                </ul>
                <div class="pager" :key="usersPage">
                  <span class="pager__info">
                    第 {{ usersPage }} / {{ usersPages }} 页 · 每页 {{ USER_PAGE_SIZE }} 条
                  </span>
                  <div class="pager__actions">
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="usersPage <= 1 || usersLoading"
                      @click="usersPrev"
                    >
                      上一页
                    </button>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="usersPage >= usersPages || usersLoading"
                      @click="usersNext"
                    >
                      下一页
                    </button>
                  </div>
                </div>
              </template>
            </section>
          </template>

          <!-- 班级 -->
          <template v-else-if="activeTab === 'groups'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">班级与权限组</h2>
                  <p class="panel__subtitle">只读 · 名称、类型与描述</p>
                </div>
                <div class="panel__actions">
                  <span class="badge badge--info">共 {{ formatCount(groupsTotal) }} 个</span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="groupsLoading"
                    @click="loadGroups"
                  >
                    {{ groupsLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <form class="toolbar" role="search" @submit.prevent="searchGroups">
                <label class="sr-only" for="groups-search">搜索班级</label>
                <input
                  id="groups-search"
                  v-model="groupsQueryDraft"
                  class="input toolbar__search"
                  type="search"
                  placeholder="搜索班级或权限组名称"
                  autocomplete="off"
                />
                <button type="submit" class="primary-button" :disabled="groupsLoading">
                  搜索
                </button>
              </form>

              <div v-if="groupsLoading && !groupsLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取班级列表…
              </div>

              <div v-else-if="groupsError && !groupsLoaded" class="state-block state-block--error" role="alert">
                <span>{{ groupsError }}</span>
                <button type="button" class="ghost-button" @click="loadGroups">重试</button>
              </div>

              <template v-else>
                <p v-if="groupsError" class="inline-note inline-note--error" role="alert">
                  {{ groupsError }}
                </p>
                <p v-if="!groups.length" class="empty-inline">没有匹配的班级或权限组。</p>
                <ul v-else class="row-list" :class="{ 'is-loading': groupsLoading }">
                  <li v-for="group in groups" :key="group._id" class="row">
                    <div class="row__main">
                      <span class="row__title">{{ group.name || '未命名组' }}</span>
                      <span class="row__meta">
                        {{ group.description || (group.permissions?.length ? group.permissions.join(' · ') : '暂无描述') }}
                      </span>
                    </div>
                    <span class="badge" :class="group.type === 'permission' ? 'badge--info' : ''">
                      {{ groupTypeText(group) }}
                    </span>
                  </li>
                </ul>
                <p class="panel__footnote">
                  接口未返回成员数量；成员明细请在主站班级页面查看。
                </p>
                <div class="pager" :key="groupsPage">
                  <span class="pager__info">
                    第 {{ groupsPage }} / {{ groupsPages }} 页 · 每页 {{ GROUP_PAGE_SIZE }} 条
                  </span>
                  <div class="pager__actions">
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="groupsPage <= 1 || groupsLoading"
                      @click="groupsPrev"
                    >
                      上一页
                    </button>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="groupsPage >= groupsPages || groupsLoading"
                      @click="groupsNext"
                    >
                      下一页
                    </button>
                  </div>
                </div>
              </template>
            </section>
          </template>

          <!-- 通知 -->
          <template v-else-if="activeTab === 'notifications'">
            <div class="panel-stack">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">发布通知</h2>
                    <p class="panel__subtitle">支持安全 Markdown；创建后立即对所有用户生效</p>
                  </div>
                  <div class="panel__actions">
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="notificationCreating"
                      @click="notificationShowPreview = !notificationShowPreview"
                    >
                      {{ notificationShowPreview ? '编辑' : '预览' }}
                    </button>
                  </div>
                </div>

                <form class="form" @submit.prevent="submitNotification">
                  <div class="field">
                    <label class="field__label" for="notification-title">标题</label>
                    <input
                      id="notification-title"
                      v-model="notificationForm.title"
                      class="input"
                      type="text"
                      maxlength="200"
                      placeholder="通知标题"
                      :aria-invalid="!!notificationFormErrors.title"
                      aria-describedby="notification-title-hint"
                      @input="notificationFormErrors.title = ''"
                    />
                    <p id="notification-title-hint" class="field__hint">
                      {{ notificationForm.title.trim().length }}/200
                    </p>
                    <p v-if="notificationFormErrors.title" class="field__error" role="alert">
                      {{ notificationFormErrors.title }}
                    </p>
                  </div>

                  <div class="field">
                    <div class="field__label-row">
                      <label class="field__label" for="notification-content">正文（Markdown）</label>
                      <span class="field__hint">支持标题、列表、引用、代码块与链接</span>
                    </div>
                    <textarea
                      v-if="!notificationShowPreview"
                      id="notification-content"
                      v-model="notificationForm.content"
                      class="textarea textarea--tall"
                      maxlength="4000"
                      placeholder="输入通知正文，支持 Markdown 语法"
                      :aria-invalid="!!notificationFormErrors.content"
                      aria-describedby="notification-content-hint"
                      @input="notificationFormErrors.content = ''"
                    ></textarea>
                    <div
                      v-else
                      class="markdown-body markdown-body--preview"
                      aria-live="polite"
                      v-html="renderMarkdown(notificationForm.content)"
                    ></div>
                    <p id="notification-content-hint" class="field__hint">
                      {{ notificationForm.content.trim().length }}/4000
                    </p>
                    <p v-if="notificationFormErrors.content" class="field__error" role="alert">
                      {{ notificationFormErrors.content }}
                    </p>
                  </div>

                  <p v-if="notificationCreateNotice" class="inline-note inline-note--success" role="status">
                    {{ notificationCreateNotice }}
                  </p>
                  <p v-if="notificationCreateError" class="inline-note inline-note--error" role="alert">
                    {{ notificationCreateError }}
                  </p>
                  <p class="panel__footnote">
                    发布与启停属于高权限操作，后端可能要求密码复核；若被拒绝，错误信息会显示在下方。
                  </p>

                  <div class="form__actions">
                    <button
                      type="submit"
                      class="primary-button"
                      :disabled="notificationCreating"
                    >
                      <span v-if="notificationCreating" class="spinner" aria-hidden="true"></span>
                      {{ notificationCreating ? '发布中…' : '发布通知' }}
                    </button>
                  </div>
                </form>
              </section>

              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">通知列表</h2>
                    <p class="panel__subtitle">
                      共 {{ notifications.length }} 条 · 版本 {{ notificationsVersion || '—' }}
                    </p>
                  </div>
                  <div class="panel__actions">
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="notificationsLoading"
                      @click="loadNotifications"
                    >
                      {{ notificationsLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div
                  v-if="notificationsLoading && !notificationsLoaded"
                  class="state-block"
                  role="status"
                >
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取通知列表…
                </div>

                <div
                  v-else-if="notificationsError && !notificationsLoaded"
                  class="state-block state-block--error"
                  role="alert"
                >
                  <span>{{ notificationsError }}</span>
                  <button type="button" class="ghost-button" @click="loadNotifications">重试</button>
                </div>

                <template v-else>
                  <p v-if="notificationsError" class="inline-note inline-note--error" role="alert">
                    {{ notificationsError }}
                  </p>
                  <p
                    v-if="notificationActionNotice"
                    class="inline-note inline-note--success"
                    role="status"
                  >
                    {{ notificationActionNotice }}
                  </p>
                  <p
                    v-if="notificationActionError"
                    class="inline-note inline-note--error"
                    role="alert"
                  >
                    {{ notificationActionError }}
                  </p>

                  <p v-if="!notifications.length" class="empty-inline">暂无通知。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': notificationsLoading }">
                    <li v-for="notice in notifications" :key="notice.id" class="notice-card">
                      <div class="notice-card__head">
                        <button
                          type="button"
                          class="notice-card__toggle"
                          :aria-expanded="notificationExpandedId === notice.id"
                          @click="toggleNotificationContent(notice.id)"
                        >
                          <span class="row__title">{{ notice.title }}</span>
                          <span class="row__meta">
                            {{ notice.author_name || '未知发布人' }} ·
                            {{ formatDateTime(notice.created_at) }}
                          </span>
                        </button>
                        <div class="notice-card__side">
                          <span class="badge" :class="notice.active ? 'badge--good' : 'badge--info'">
                            {{ notificationStatusText(notice) }}
                          </span>
                          <button
                            type="button"
                            class="ghost-button ghost-button--small"
                            role="switch"
                            :aria-checked="notice.active"
                            :aria-label="notice.active ? '停用通知' : '启用通知'"
                            :disabled="notificationBusyId === notice.id"
                            @click="toggleNotificationActive(notice)"
                          >
                            {{
                              notificationBusyId === notice.id
                                ? '处理中…'
                                : notice.active
                                  ? '停用'
                                  : '启用'
                            }}
                          </button>
                        </div>
                      </div>
                      <div
                        v-if="notificationExpandedId === notice.id"
                        class="markdown-body notice-card__body"
                        v-html="notificationHtml(notice)"
                      ></div>
                    </li>
                  </ul>
                </template>
              </section>
            </div>
          </template>

          <!-- 角色 -->
          <template v-else-if="activeTab === 'roles'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">角色策略</h2>
                  <p class="panel__subtitle">只读 · 功能开关与月度 AI 配额由后端策略表下发</p>
                </div>
                <div class="panel__actions">
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="rolesLoading"
                    @click="loadRolePolicies"
                  >
                    {{ rolesLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="rolesLoading && !rolesLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取角色策略…
              </div>

              <div v-else-if="rolesError && !rolesLoaded" class="state-block state-block--error" role="alert">
                <span>{{ rolesError }}</span>
                <button type="button" class="ghost-button" @click="loadRolePolicies">重试</button>
              </div>

              <template v-else>
                <p v-if="rolesError" class="inline-note inline-note--error" role="alert">
                  {{ rolesError }}
                </p>
                <p v-if="!sortedRolePolicies.length" class="empty-inline">暂无角色策略。</p>
                <div v-else class="role-grid">
                  <article
                    v-for="(policy, index) in sortedRolePolicies"
                    :key="policy.role"
                    class="role-card surface-enter"
                    :style="{ '--i': index }"
                  >
                    <header class="role-card__head">
                      <div>
                        <h3 class="role-card__name">{{ roleLabel(policy.role) }}</h3>
                        <span class="role-card__code">{{ policy.role }}</span>
                      </div>
                      <span class="badge badge--info">
                        {{ policy.role === 'admin' ? '固定全量' : '策略行' }}
                      </span>
                    </header>

                    <div class="quota-row">
                      <div
                        v-for="kind in ['af', 'schedule']"
                        :key="kind"
                        class="quota"
                      >
                        <span class="quota__label">{{ QUOTA_LABELS[kind] }}</span>
                        <span class="quota__value">{{ quotaText(policy.quotas?.[kind]) }}</span>
                      </div>
                    </div>

                    <div class="feature-grid">
                      <span
                        v-for="key in roleFeatureKeys"
                        :key="key"
                        class="chip"
                        :class="featureEnabled(policy, key) ? 'chip--on' : 'chip--off'"
                        :title="`${featureLabel(key)} · ${featureEnabled(policy, key) ? '已启用' : '未启用'}`"
                      >
                        <span class="chip__dot" aria-hidden="true"></span>
                        {{ featureLabel(key) }}
                        <span class="chip__state">{{ featureEnabled(policy, key) ? '开' : '关' }}</span>
                      </span>
                    </div>

                    <p class="role-card__foot">
                      更新：{{ formatDateTime(policy.updated_at) }}
                      <template v-if="policy.updated_by"> · {{ shortOid(policy.updated_by) }}</template>
                    </p>
                  </article>
                </div>
                <p class="panel__footnote">
                  只读视图；修改角色策略需要另一位管理员的动态码，请在主站完成。
                </p>
              </template>
            </section>
          </template>

          <!-- 日志 -->
          <template v-else-if="activeTab === 'logs'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">审计日志</h2>
                  <p class="panel__subtitle">只读 · 时间、类型、级别、操作者与详情</p>
                </div>
                <div class="panel__actions">
                  <span class="badge badge--info">共 {{ formatCount(logsTotal) }} 条</span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="logsLoading"
                    @click="loadLogs"
                  >
                    {{ logsLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <form class="toolbar" role="search" @submit.prevent="searchLogs">
                <label class="sr-only" for="logs-search">搜索日志</label>
                <input
                  id="logs-search"
                  v-model="logsQueryDraft"
                  class="input toolbar__search"
                  type="search"
                  placeholder="搜索 URL 或日志详情"
                  autocomplete="off"
                />
                <button type="submit" class="primary-button" :disabled="logsLoading">搜索</button>
              </form>

              <div v-if="logsLoading && !logsLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取日志…
              </div>

              <div v-else-if="logsError && !logsLoaded" class="state-block state-block--error" role="alert">
                <span>{{ logsError }}</span>
                <button type="button" class="ghost-button" @click="loadLogs">重试</button>
              </div>

              <template v-else>
                <p v-if="logsError" class="inline-note inline-note--error" role="alert">
                  {{ logsError }}
                </p>
                <p v-if="!logs.length" class="empty-inline">没有匹配的日志。</p>
                <ul v-else class="row-list" :class="{ 'is-loading': logsLoading }">
                  <li v-for="log in logs" :key="log._id" class="row row--stacked">
                    <div class="row__main">
                      <span class="row__title">
                        {{ log.type || '未分类日志' }}
                        <span class="badge" :class="logLevelClass(log.level)">
                          {{ logLevelText(log.level) }}
                        </span>
                      </span>
                      <span class="row__meta">
                        {{ logPerformer(log) }} · {{ formatUnixSeconds(log.timestamp) }}
                      </span>
                      <span v-if="log.data" class="row__detail" :title="log.data">
                        {{ log.data }}
                      </span>
                    </div>
                  </li>
                </ul>
                <div class="pager" :key="logsPage">
                  <span class="pager__info">
                    第 {{ logsPage }} / {{ logsPages }} 页 · 每页 {{ LOG_PAGE_SIZE }} 条
                  </span>
                  <div class="pager__actions">
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="logsPage <= 1 || logsLoading"
                      @click="logsPrev"
                    >
                      上一页
                    </button>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="logsPage >= logsPages || logsLoading"
                      @click="logsNext"
                    >
                      下一页
                    </button>
                  </div>
                </div>
              </template>
            </section>
          </template>

          <!-- 设置 -->
          <template v-else-if="activeTab === 'settings'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">系统设置</h2>
                  <p class="panel__subtitle">
                    可编辑开关、整数与文本；列表 / 字典项只读展示
                  </p>
                </div>
                <div class="panel__actions">
                  <span class="badge badge--info">
                    已改动 {{ settingChangedCount }} 项
                  </span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="settingsLoading || settingsSaving"
                    @click="loadSettings"
                  >
                    {{ settingsLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="settingsLoading && !settingsLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取系统设置…
              </div>

              <div v-else-if="settingsError && !settingsLoaded" class="state-block state-block--error" role="alert">
                <span>{{ settingsError }}</span>
                <button type="button" class="ghost-button" @click="loadSettings">重试</button>
              </div>

              <template v-else>
                <p v-if="settingsError" class="inline-note inline-note--error" role="alert">
                  {{ settingsError }}
                </p>
                <p
                  v-if="settingsSaveNotice"
                  class="inline-note inline-note--success"
                  role="status"
                >
                  {{ settingsSaveNotice }}
                </p>
                <p
                  v-if="settingsSaveError"
                  class="inline-note inline-note--error"
                  role="alert"
                >
                  {{ settingsSaveError }}
                </p>

                <p v-if="!settings?.items?.length" class="empty-inline">暂无可显示的设置项。</p>

                <div v-else class="settings-stack">
                  <section
                    v-for="(group, groupIndex) in settingGroups"
                    :key="group.group"
                    class="sub-panel surface-enter"
                    :style="{ '--i': groupIndex }"
                  >
                    <div class="sub-panel__head">
                      <h3 class="sub-panel__title">{{ group.label }}</h3>
                      <span class="sub-panel__meta">{{ group.items.length }} 项</span>
                    </div>

                    <div class="setting-list">
                      <div
                        v-for="item in group.items"
                        :key="item.key"
                        class="setting-row"
                      >
                        <div class="setting-row__body">
                          <div class="setting-row__head">
                            <span class="setting-row__label">{{ item.label }}</span>
                            <span class="setting-row__type">{{ settingTypeText(item) }}</span>
                            <span
                              v-if="isSettingDirty(item)"
                              class="badge badge--warn"
                            >
                              未保存
                            </span>
                          </div>
                          <span class="setting-row__key">{{ item.key }}</span>
                          <p v-if="item.description" class="setting-row__description">
                            {{ item.description }}
                          </p>
                          <p v-if="settingRangeText(item)" class="field__hint">
                            {{ settingRangeText(item) }}
                          </p>
                          <p
                            v-if="!isSettingEditable(item)"
                            class="setting-row__readonly"
                          >
                            只读：{{ settingValueText(item.value) }}
                          </p>
                        </div>

                        <div class="setting-row__control">
                          <template v-if="item.type === 'bool'">
                            <label class="switch">
                              <input
                                v-model="settingDrafts[item.key].bool"
                                type="checkbox"
                                :aria-label="item.label"
                              />
                              <span class="switch__track" aria-hidden="true">
                                <span class="switch__thumb"></span>
                              </span>
                            </label>
                          </template>
                          <template v-else-if="item.type === 'int' || item.type === 'float'">
                            <input
                              v-model="settingDrafts[item.key].text"
                              class="input setting-row__input"
                              type="number"
                              :step="item.type === 'float' ? 'any' : '1'"
                              inputmode="numeric"
                              :min="item.min ?? undefined"
                              :max="item.max ?? undefined"
                              :aria-label="item.label"
                            />
                          </template>
                          <template v-else-if="item.type === 'str'">
                            <input
                              v-model="settingDrafts[item.key].text"
                              class="input setting-row__input"
                              type="text"
                              :aria-label="item.label"
                            />
                          </template>
                          <span v-else class="badge">只读</span>
                        </div>
                      </div>
                    </div>
                  </section>

                  <p class="panel__footnote">
                    共 {{ editableSettingCount }} 项可编辑、{{ readOnlySettingCount }} 项只读。
                    保存时只提交发生变化的项；写操作可能需要密码复核。
                  </p>

                  <div class="form__actions">
                    <button
                      type="button"
                      class="primary-button"
                      :disabled="settingsSaving || settingChangedCount === 0"
                      @click="saveSettings"
                    >
                      <span v-if="settingsSaving" class="spinner" aria-hidden="true"></span>
                      {{ settingsSaving ? '保存中…' : `保存改动（${settingChangedCount}）` }}
                    </button>
                  </div>
                </div>
              </template>
            </section>
          </template>

          <!-- 封禁 -->
          <template v-else-if="activeTab === 'ban'">
            <div class="panel-stack">
              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">封禁规则</h2>
                    <p class="panel__subtitle">只读 · 触发条件、启用状态与梯度</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">{{ banRules.length }} 条</span>
                    <button
                      type="button"
                      class="ghost-button"
                      :disabled="banLoading"
                      @click="loadBan"
                    >
                      {{ banLoading ? '刷新中…' : '刷新' }}
                    </button>
                  </div>
                </div>

                <div v-if="banLoading && !banLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取封禁数据…
                </div>

                <template v-else>
                  <p v-if="banRulesError" class="inline-note inline-note--error" role="alert">
                    {{ banRulesError }}
                  </p>
                  <p v-if="!banRules.length" class="empty-inline">暂无封禁规则。</p>
                  <ul v-else class="row-list" :class="{ 'is-loading': banLoading }">
                    <li v-for="rule in banRules" :key="rule.code" class="row">
                      <div class="row__main">
                        <span class="row__title">{{ rule.name || rule.code }}</span>
                        <span class="row__meta">
                          {{ banTriggerText(rule) }} · {{ banPenaltiesText(rule) }}
                          <template v-if="rule.builtin"> · 内置</template>
                        </span>
                      </div>
                      <span class="badge" :class="rule.enabled ? 'badge--good' : 'badge--info'">
                        {{ rule.enabled ? '已启用' : '已停用' }}
                      </span>
                    </li>
                  </ul>
                </template>
              </section>

              <section class="panel">
                <div class="panel__header">
                  <div>
                    <h2 class="panel__title">封禁记录</h2>
                    <p class="panel__subtitle">只读 · 用户、原因、来源与到期时间</p>
                  </div>
                  <div class="panel__actions">
                    <span class="badge badge--info">共 {{ formatCount(banTotal) }} 条</span>
                  </div>
                </div>

                <form class="toolbar" role="search" @submit.prevent="searchBanRecords">
                  <label class="sr-only" for="ban-search">搜索封禁记录</label>
                  <input
                    id="ban-search"
                    v-model="banQueryDraft"
                    class="input toolbar__search"
                    type="search"
                    placeholder="搜索姓名或学号"
                    autocomplete="off"
                  />
                  <button type="submit" class="primary-button" :disabled="banRecordsLoading">
                    搜索
                  </button>
                </form>

                <p v-if="banRecordsError" class="inline-note inline-note--error" role="alert">
                  {{ banRecordsError }}
                </p>
                <div v-if="banLoading && !banLoaded" class="state-block" role="status">
                  <span class="spinner" aria-hidden="true"></span>
                  正在读取封禁记录…
                </div>
                <template v-else>
                  <p v-if="!banRecords.length" class="empty-inline">没有匹配的封禁记录。</p>
                  <ul
                    v-else
                    class="row-list"
                    :class="{ 'is-loading': banLoading || banRecordsLoading }"
                  >
                    <li v-for="record in banRecords" :key="record._id" class="row row--stacked">
                      <div class="row__main">
                        <span class="row__title">
                          {{ record.user_name || '未知用户' }}
                          <span class="row__id">{{ record.user_id }}</span>
                        </span>
                        <span class="row__meta">
                          {{ banRecordSource(record) }} · {{ record.reason || '未填写原因' }}
                          <template v-if="record.operator_name"> · 操作人 {{ record.operator_name }}</template>
                        </span>
                        <span class="row__detail">
                          开始 {{ formatDateTime(record.started_at) }} · 到期
                          {{ banRecordExpiry(record) }}
                        </span>
                      </div>
                    </li>
                  </ul>
                  <div class="pager" :key="banPage">
                    <span class="pager__info">
                      第 {{ banPage }} / {{ banPages }} 页 · 每页 {{ BAN_PAGE_SIZE }} 条
                    </span>
                    <div class="pager__actions">
                      <button
                        type="button"
                        class="ghost-button"
                        :disabled="banPage <= 1 || banRecordsLoading"
                        @click="banPrev"
                      >
                        上一页
                      </button>
                      <button
                        type="button"
                        class="ghost-button"
                        :disabled="banPage >= banPages || banRecordsLoading"
                        @click="banNext"
                      >
                        下一页
                      </button>
                    </div>
                  </div>
                </template>
              </section>
            </div>
          </template>

          <!-- 附件 -->
          <template v-else-if="activeTab === 'attachments'">
            <section class="panel">
              <div class="panel__header">
                <div>
                  <h2 class="panel__title">附件清单</h2>
                  <p class="panel__subtitle">只读 · scope=all · 文件名、用途、状态、所有者与时间</p>
                </div>
                <div class="panel__actions">
                  <span class="badge badge--info">共 {{ formatCount(attachmentsTotal) }} 个</span>
                  <button
                    type="button"
                    class="ghost-button"
                    :disabled="attachmentsLoading"
                    @click="loadAttachments"
                  >
                    {{ attachmentsLoading ? '刷新中…' : '刷新' }}
                  </button>
                </div>
              </div>

              <div v-if="attachmentsLoading && !attachmentsLoaded" class="state-block" role="status">
                <span class="spinner" aria-hidden="true"></span>
                正在读取附件列表…
              </div>

              <div
                v-else-if="attachmentsError && !attachmentsLoaded"
                class="state-block state-block--error"
                role="alert"
              >
                <span>{{ attachmentsError }}</span>
                <button type="button" class="ghost-button" @click="loadAttachments">重试</button>
              </div>

              <template v-else>
                <p v-if="attachmentsError" class="inline-note inline-note--error" role="alert">
                  {{ attachmentsError }}
                </p>
                <p v-if="!attachments.length" class="empty-inline">暂无附件。</p>
                <ul v-else class="row-list" :class="{ 'is-loading': attachmentsLoading }">
                  <li v-for="file in attachments" :key="file._id" class="row">
                    <div class="row__main">
                      <span class="row__title">{{ file.original_name || file._id }}</span>
                      <span class="row__meta">
                        {{ file.purpose || '未标注用途' }} · {{ formatBytes(file.size_bytes) }} ·
                        所有者 {{ shortOid(file.owner_oid) }}
                      </span>
                      <span class="row__detail">
                        {{ formatDateTime(file.created_at) }} · {{ file.mime || '未知类型' }}
                      </span>
                    </div>
                    <span class="badge" :class="attachmentStatusClass(file.status)">
                      {{ attachmentStatusText(file.status) }}
                    </span>
                  </li>
                </ul>
                <p class="panel__footnote">
                  仅展示第一页前 {{ ATTACHMENT_PAGE_SIZE }} 条；其余附件请在主站附件管理查看。
                </p>
              </template>
            </section>
          </template>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.manage {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.manage__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
}

.manage__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
}

.manage__back {
  position: relative;
  width: 44px;
  height: 44px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.48) 100%
  );
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  cursor: pointer;
  overflow: hidden;
  isolation: isolate;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    inset 0 -1px 0 rgba(0, 0, 0, 0.06),
    0 2px 8px rgba(18, 48, 82, 0.08);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.manage__back::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--glass-highlight);
  border-radius: var(--radius-md);
  opacity: 0.6;
  pointer-events: none;
  z-index: -1;
}

.manage__back:hover {
  color: var(--color-brand);
  transform: scale(1.05);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.98),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(18, 48, 82, 0.12);
}

.manage__back:active {
  transform: scale(0.95);
}

.manage__back:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}

.manage__heading {
  flex: 1 1 220px;
  min-width: 0;
}

.manage__title {
  font-size: var(--font-size-title-3);
  font-weight: 600;
  line-height: var(--line-height-snug);
  letter-spacing: var(--letter-spacing-tight);
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.manage__subtitle {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.manage__header-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  align-self: center;
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.78);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.86);
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tone-blue);
  box-shadow: 0 0 0 3px var(--tone-blue-bg);
}

.manage__tabs {
  margin-bottom: var(--space-4);
  min-width: 0;
}

.manage__content {
  min-height: 420px;
}

/* -------------------------------------------------------------------------- */
/* Panels                                                                      */
/* -------------------------------------------------------------------------- */

.panel {
  position: relative;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  padding: var(--space-5);
  box-shadow: var(--glass-shadow);
  isolation: isolate;
  overflow: hidden;
}

.panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: var(--glass-highlight);
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  opacity: 0.38;
  pointer-events: none;
}

.panel > * {
  position: relative;
  z-index: 1;
}

.panel-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}

.panel__title {
  font-size: var(--font-size-headline);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
}

.panel__subtitle {
  margin-top: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
}

.panel__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.panel__footnote {
  margin-top: var(--space-3);
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

/* -------------------------------------------------------------------------- */
/* Buttons, fields, toolbar                                                    */
/* -------------------------------------------------------------------------- */

.ghost-button,
.primary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
  border: 1px solid rgba(255, 255, 255, 0.82);
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.72) 0%,
    rgba(232, 242, 252, 0.5) 100%
  );
  color: var(--color-text-primary);
  font-family: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.86),
    0 2px 8px rgba(18, 48, 82, 0.07);
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.ghost-button:hover:not(:disabled),
.primary-button:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    0 4px 12px rgba(18, 48, 82, 0.11);
}

.ghost-button:active:not(:disabled),
.primary-button:active:not(:disabled) {
  transform: scale(0.97);
}

.ghost-button:disabled,
.primary-button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
}

.ghost-button:focus-visible,
.primary-button:focus-visible,
.text-button:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.ghost-button--small {
  min-height: 32px;
  padding: 0 var(--space-3);
  font-size: var(--font-size-caption);
}

.primary-button {
  border-color: rgba(0, 113, 227, 0.22);
  background: linear-gradient(135deg, #409cff 0%, #0071e3 100%);
  color: #ffffff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.34),
    0 3px 12px rgba(0, 113, 227, 0.24);
}

.primary-button:hover:not(:disabled) {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 18px rgba(0, 113, 227, 0.3);
}

.text-button {
  min-height: 28px;
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-brand);
  font-family: inherit;
  font-size: var(--font-size-caption);
  font-weight: 600;
  cursor: pointer;
}

.text-button:hover {
  background: var(--tone-blue-bg);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-4);
}

.toolbar__search {
  flex: 1 1 240px;
  min-width: 0;
}

.toolbar__select {
  flex: 0 1 170px;
  min-width: 130px;
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.form__actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field__label-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.field__label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.input,
.textarea {
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

.textarea {
  min-height: 86px;
  resize: vertical;
}

.textarea--tall {
  min-height: 180px;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
}

.input:focus-visible,
.textarea:focus-visible,
select.input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 1px;
  border-color: transparent;
}

.field__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
}

.field__error {
  font-size: var(--font-size-caption);
  color: var(--tone-rose);
  line-height: var(--line-height-normal);
}

/* -------------------------------------------------------------------------- */
/* Badges and states                                                           */
/* -------------------------------------------------------------------------- */

.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  background: var(--tone-ink-bg);
  color: var(--tone-ink);
  font-size: var(--font-size-caption);
  font-weight: 600;
  line-height: 1.3;
  white-space: nowrap;
}

.badge--good {
  background: var(--tone-green-bg);
  color: #1d7a34;
}

.badge--warn {
  background: var(--tone-amber-bg);
  color: #a86400;
}

.badge--bad {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.badge--info {
  background: var(--tone-blue-bg);
  color: var(--color-brand);
}

.state-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-5);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.44);
  border: 1px solid rgba(255, 255, 255, 0.68);
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
}

.state-block--error {
  border-color: rgba(255, 59, 48, 0.22);
  background: rgba(255, 59, 48, 0.06);
  color: var(--tone-rose);
}

.inline-note {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
  margin-top: var(--space-4);
}

.inline-note--error {
  background: var(--tone-rose-bg);
  color: var(--tone-rose);
}

.inline-note--warn {
  background: var(--tone-amber-bg);
  color: #8a5600;
}

.inline-note--success {
  background: var(--tone-green-bg);
  color: #1d7a34;
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

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* -------------------------------------------------------------------------- */
/* Summary and overview                                                        */
/* -------------------------------------------------------------------------- */

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-4);
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 108px;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.48);
  border: 1px solid rgba(255, 255, 255, 0.72);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.86),
    0 2px 8px rgba(18, 48, 82, 0.04);
}

.summary-card__label {
  font-size: var(--font-size-caption);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.summary-card__value {
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.1;
  color: var(--color-text-primary);
  word-break: break-word;
  font-variant-numeric: tabular-nums;
}

.summary-card__hint {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
  word-break: break-word;
}

.overview-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-4);
}

.sub-panel {
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.42);
  border: 1px solid rgba(255, 255, 255, 0.66);
}

.sub-panel__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}

.sub-panel__title {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
}

.sub-panel__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

/* -------------------------------------------------------------------------- */
/* Compact list rows                                                           */
/* -------------------------------------------------------------------------- */

.row-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  transition: opacity var(--duration-fast) var(--ease-smooth);
}

.row-list.is-loading {
  opacity: 0.55;
}

.row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-2);
  border-bottom: 1px solid var(--color-border-hairline);
  min-width: 0;
}

.row:last-child {
  border-bottom: none;
}

.row--compact {
  padding: var(--space-2);
}

.row--stacked {
  align-items: flex-start;
}

.row__main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.row__title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: var(--line-height-snug);
  overflow-wrap: anywhere;
}

.row__id {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text-tertiary);
}

.row__meta {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
  overflow-wrap: anywhere;
}

.row__detail {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
  line-height: var(--line-height-normal);
  overflow-wrap: anywhere;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-3);
  animation: pager-in 300ms var(--ease-spring);
}

@keyframes pager-in {
  from {
    opacity: 0.55;
    transform: translateY(3px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.pager__info {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}

.pager__actions {
  display: flex;
  gap: var(--space-2);
}

/* -------------------------------------------------------------------------- */
/* Notifications                                                               */
/* -------------------------------------------------------------------------- */

.notice-card {
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--color-border-hairline);
}

.notice-card:last-child {
  border-bottom: none;
}

.notice-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-2);
  flex-wrap: wrap;
}

.notice-card__toggle {
  flex: 1 1 240px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.notice-card__toggle:hover .row__title {
  color: var(--color-brand);
}

.notice-card__toggle:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.notice-card__side {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.notice-card__body {
  padding: 0 var(--space-2) var(--space-4);
}

.markdown-body {
  font-size: var(--font-size-callout);
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
  overflow-wrap: anywhere;
}

.markdown-body--preview {
  min-height: 180px;
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.52);
  border: 1px solid var(--color-border-hairline);
}

.markdown-body :deep(p) {
  margin: 0 0 var(--space-3);
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  color: var(--color-text-primary);
  margin: var(--space-4) 0 var(--space-2);
  line-height: var(--line-height-snug);
}

.markdown-body :deep(h1) {
  font-size: var(--font-size-headline);
}

.markdown-body :deep(h2) {
  font-size: var(--font-size-body-large);
}

.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-size: var(--font-size-body);
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0 0 var(--space-3);
  padding-left: 1.4em;
}

.markdown-body :deep(blockquote) {
  margin: var(--space-3) 0;
  padding: var(--space-3) var(--space-4);
  background: rgba(255, 255, 255, 0.34);
  border-left: 2px solid var(--color-brand);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.markdown-body :deep(code) {
  padding: 2px 5px;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 5px;
  font-size: 0.92em;
}

.markdown-body :deep(pre) {
  margin: var(--space-3) 0;
  padding: var(--space-4);
  overflow-x: auto;
  background: rgba(18, 32, 48, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: var(--radius-md);
}

.markdown-body :deep(pre code) {
  padding: 0;
  background: transparent;
}

.markdown-body :deep(a) {
  color: var(--color-brand);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-body :deep(hr) {
  margin: var(--space-4) 0;
  border: 0;
  border-top: 1px solid var(--color-border-subtle);
}

.markdown-body :deep(strong) {
  color: var(--color-text-primary);
}

/* -------------------------------------------------------------------------- */
/* Roles                                                                       */
/* -------------------------------------------------------------------------- */

.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-4);
}

.role-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.46);
  border: 1px solid rgba(255, 255, 255, 0.7);
}

.role-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.role-card__name {
  font-size: var(--font-size-body-large);
  font-weight: 600;
  color: var(--color-text-primary);
}

.role-card__code {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.quota-row {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.quota {
  flex: 1 1 120px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid var(--color-border-hairline);
}

.quota__label {
  font-size: var(--font-size-caption);
  color: var(--color-text-tertiary);
}

.quota__value {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.feature-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.3;
  border: 1px solid transparent;
}

.chip--on {
  background: var(--tone-green-bg);
  color: #1d7a34;
  border-color: rgba(52, 199, 89, 0.2);
}

.chip--off {
  background: rgba(110, 110, 115, 0.08);
  color: var(--color-text-tertiary);
  border-color: rgba(110, 110, 115, 0.12);
}

.chip__dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.chip__state {
  font-size: 10px;
  opacity: 0.72;
  border-left: 1px solid currentColor;
  padding-left: 5px;
  margin-left: 1px;
}

.role-card__foot {
  margin-top: auto;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

/* -------------------------------------------------------------------------- */
/* Settings                                                                    */
/* -------------------------------------------------------------------------- */

.settings-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  margin-top: var(--space-4);
}

.setting-list {
  display: flex;
  flex-direction: column;
}

.setting-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border-hairline);
}

.setting-row:last-child {
  border-bottom: none;
}

.setting-row__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.setting-row__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.setting-row__label {
  font-size: var(--font-size-callout);
  font-weight: 600;
  color: var(--color-text-primary);
}

.setting-row__type {
  font-size: 11px;
  color: var(--color-text-tertiary);
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid var(--color-border-hairline);
}

.setting-row__key {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  color: var(--color-text-tertiary);
  overflow-wrap: anywhere;
}

.setting-row__description,
.setting-row__readonly {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: var(--line-height-normal);
  margin: 0;
}

.setting-row__readonly {
  white-space: pre-wrap;
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 11px;
  padding: var(--space-2);
  background: rgba(255, 255, 255, 0.44);
  border-radius: var(--radius-sm);
  max-height: 180px;
  overflow: auto;
}

.setting-row__control {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 120px;
  padding-top: 2px;
}

.setting-row__input {
  width: 160px;
}

.switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

.switch input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.switch__track {
  position: relative;
  display: block;
  width: 46px;
  height: 28px;
  border-radius: var(--radius-pill);
  background: rgba(110, 110, 115, 0.24);
  border: 1px solid rgba(255, 255, 255, 0.6);
  transition: background var(--duration-fast) var(--ease-smooth);
}

.switch__thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(18, 48, 82, 0.22);
  transition: transform var(--duration-fast) var(--ease-spring);
}

.switch input:checked + .switch__track {
  background: var(--color-brand);
}

.switch input:checked + .switch__track .switch__thumb {
  transform: translateX(18px);
}

.switch input:focus-visible + .switch__track {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

/* -------------------------------------------------------------------------- */
/* Transitions and responsive                                                  */
/* -------------------------------------------------------------------------- */

.tab-fade-enter-active,
.tab-fade-leave-active {
  transition:
    opacity 160ms var(--ease-smooth),
    transform 160ms var(--ease-smooth);
}

.tab-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.tab-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 640px) {
  .manage {
    padding: var(--space-5) var(--space-4) var(--space-9);
  }

  .manage__title {
    font-size: 26px;
  }

  .panel {
    padding: var(--space-4);
    border-radius: var(--radius-lg);
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .row {
    flex-direction: column;
    gap: var(--space-2);
  }

  .row > .badge {
    align-self: flex-start;
  }

  .setting-row {
    flex-direction: column;
    gap: var(--space-2);
  }

  .setting-row__control {
    width: 100%;
    justify-content: flex-start;
    padding-top: 0;
  }

  .setting-row__input {
    width: 100%;
  }

  .notice-card__head {
    flex-direction: column;
  }

  .notice-card__side {
    width: 100%;
    justify-content: space-between;
  }

  .pager {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (prefers-reduced-motion: reduce) {
  .manage__back,
  .ghost-button,
  .primary-button,
  .chip,
  .switch__track,
  .switch__thumb,
  .tab-fade-enter-active,
  .tab-fade-leave-active {
    transition: none;
  }

  .spinner,
  .pager {
    animation: none;
  }

  .manage__back:hover,
  .ghost-button:hover:not(:disabled),
  .primary-button:hover:not(:disabled) {
    transform: none;
  }
}

@supports not (backdrop-filter: blur(14px)) {
  .panel,
  .manage__back,
  .manage__header-status,
  .summary-card,
  .sub-panel,
  .role-card,
  .state-block {
    background: rgba(255, 255, 255, 0.92);
  }

  .ghost-button {
    background: rgba(255, 255, 255, 0.94);
  }

  .primary-button {
    background: #0071e3;
  }
}
</style>
