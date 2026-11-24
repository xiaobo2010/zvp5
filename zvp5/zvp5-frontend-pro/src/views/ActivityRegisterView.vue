<script setup lang="ts">
import BubbleFeedback from '@/components/BubbleFeedback.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  addActivityMembers,
  listActivityMembers,
  listAdminActivities,
  lookupStudent,
  updateActivityMember
} from '@/api/activity-admin'
import type {
  ActivityMemberRow,
  AdminActivity,
  RegistrationMode
} from '@/api/activity-admin'
import { ApiError, readableError } from '@/api/http'

interface AddRow {
  key: string
  studentId: string
  name: string
  oid: string
  /** `<input type="number">` v-model yields a number even though the field starts as ''. */
  duration: string | number
  mode: RegistrationMode
  checking: boolean
  error: string
  /**
   * 查询代次。输入一变就 +1，让还在路上的查询响应作废——否则「先输入 A，
   * 没等结果就改成 B」时，A 的响应会晚到并覆盖 B 的匹配。
   */
  seq: number
  /** 当前行在途的查询；提交时复用它，而不是再打一次同样的请求。 */
  pending: Promise<void> | null
  pendingValue: string
}

interface EditRow {
  key: string
  memberId: string
  studentId: string
  name: string
  duration: string
  originalDuration: number
  /**
   * 加载时输入框里的原始文本。判断「用户到底动没动这一行」只能看文本：
   * 历史数据里 `duration` 可能是 `''`/`0`，用 `String(originalDuration)`
   * 比较会把空的原始值误判成「改成了非法时长」，从而挡住整批保存。
   */
  originalDurationText: string
  mode: RegistrationMode
  originalMode: RegistrationMode
  saving: boolean
  message: string
}

const route = useRoute()
const router = useRouter()

const mode = computed<'add' | 'modify'>(() => (route.params.mode === 'modify' ? 'modify' : 'add'))
const pageTitle = computed(() => (mode.value === 'add' ? '添加义工' : '修改义工'))
const pageSubtitle = computed(() =>
  mode.value === 'add'
    ? '选择活动后，填写学号、时长和义工类型；已存在的成员不会被重复添加。'
    : '选择活动后，修改已录入成员的时长与类型；只覆盖这一条记录。'
)

const activities = ref<AdminActivity[]>([])
const activityQuery = ref('')
const selectedActivityId = ref('')
const activitiesLoading = ref(false)
const activitiesError = ref('')
const selectedActivity = computed(
  () => activities.value.find((activity) => activity._id === selectedActivityId.value) ?? null
)

const addRows = ref<AddRow[]>([])
const editRows = ref<EditRow[]>([])
const membersTotal = ref(0)
const membersLoading = ref(false)
const membersError = ref('')
const submitting = ref(false)
const feedback = ref('')
const feedbackType = ref<'success' | 'error'>('success')

const MODE_LABELS: Record<RegistrationMode, string> = {
  'on-campus': '校内',
  'off-campus': '校外',
  'social-practice': '社会实践'
}

const modeOptions = computed<RegistrationMode[]>(() => {
  const type = selectedActivity.value?.type
  if (type === 'on-campus' || type === 'off-campus' || type === 'social-practice') {
    return [type]
  }
  return ['on-campus', 'off-campus', 'social-practice']
})

/**
 * 编辑行的类型下拉：把记录现有的模式并进选项。
 *
 * 历史数据里可能存在与活动类型不一致的模式；如果选项里没有它，下拉会显示成
 * 另一个值、模型却还是旧值，保存时被后端以「type and member mode do not match」
 * 拒绝。并进选项后既能如实显示，也能让用户改成合法模式。
 */
function editModeOptions(row: EditRow): RegistrationMode[] {
  return modeOptions.value.includes(row.originalMode)
    ? modeOptions.value
    : [row.originalMode, ...modeOptions.value]
}

// The v3 member list caps a page at 100 rows; make that limit explicit instead
// of silently hiding the rest of a large activity.
const membersTruncated = computed(
  () => editRows.value.length >= 100 && membersTotal.value > editRows.value.length
)

let keySeed = 0
function nextKey() {
  keySeed += 1
  return `row-${keySeed}`
}

function createAddRow(): AddRow {
  return {
    key: nextKey(),
    studentId: '',
    name: '',
    oid: '',
    duration: '',
    mode: modeOptions.value[0] ?? 'on-campus',
    checking: false,
    error: '',
    seq: 0,
    pending: null,
    pendingValue: ''
  }
}

function resetAddRows() {
  addRows.value = Array.from({ length: 3 }, () => createAddRow())
}

function setFeedback(message: string, type: 'success' | 'error') {
  feedback.value = message
  feedbackType.value = type
}

function formatActivityMeta(activity: AdminActivity): string {
  const date = activity.date ? activity.date.slice(0, 10) : ''
  return [date, activity.place].filter(Boolean).join(' · ')
}

function normalizeMode(value: string): RegistrationMode {
  return value === 'off-campus' || value === 'social-practice' ? value : 'on-campus'
}

async function loadActivities() {
  activitiesLoading.value = true
  activitiesError.value = ''
  try {
    const rows = await listAdminActivities(activityQuery.value.trim(), 1, 80)
    activities.value = rows.filter((activity) => activity.status !== 'refused')
    const requested = typeof route.query.activity === 'string' ? route.query.activity : ''
    if (!selectedActivityId.value && requested) selectedActivityId.value = requested
    if (!selectedActivityId.value && activities.value.length === 1) {
      selectedActivityId.value = activities.value[0]?._id ?? ''
    }
    await handleActivityChange()
  } catch (reason) {
    activitiesError.value = readableError(reason, '活动列表加载失败。')
  } finally {
    activitiesLoading.value = false
  }
}

async function handleActivityChange() {
  feedback.value = ''
  if (!selectedActivityId.value) return
  const activity = selectedActivity.value
  if (!activity) return
  if (mode.value === 'add') {
    resetAddRows()
    return
  }
  await loadMembers()
}

async function loadMembers() {
  if (!selectedActivity.value) return
  membersLoading.value = true
  membersError.value = ''
  try {
    const result = await listActivityMembers(selectedActivity.value._id, '', 1, 100)
    membersTotal.value = result.total
    editRows.value = result.members.map((member: ActivityMemberRow) => ({
      key: nextKey(),
      memberId: member._id,
      studentId: String(member.user?.id ?? ''),
      name: member.member_name || member.user?.name || '未命名成员',
      duration: String(member.duration ?? ''),
      originalDuration: Number(member.duration) || 0,
      originalDurationText: String(member.duration ?? ''),
      mode: normalizeMode(member.mode),
      originalMode: normalizeMode(member.mode),
      saving: false,
      message: ''
    }))
  } catch (reason) {
    membersError.value = readableError(reason, '成员列表加载失败。')
  } finally {
    membersLoading.value = false
  }
}

/**
 * 清掉一行的匹配结果，并让在途查询作废。
 *
 * 输入框一改就必须调用它：否则用户把「2026001」改成「2026002」后直接点提交，
 * 校验和 payload 用的还是上一个学号的 oid——把时长记到别人头上。
 */
function invalidateMatch(row: AddRow) {
  row.seq += 1
  row.oid = ''
  row.name = ''
  row.error = ''
  row.checking = false
  row.pending = null
  row.pendingValue = ''
}

function onStudentInput(row: AddRow) {
  invalidateMatch(row)
}

/**
 * 解析一行的学号/姓名。
 *
 * - `force` 未开时，已经匹配过的行不重复查询（blur 反复触发也只打一次）。
 * - 同一行同一取值的在途查询会被复用，blur 与「提交」同时触发不会打出两次请求。
 * - 响应回来先比对 `seq`，过期结果直接丢弃。
 */
function resolveRow(row: AddRow, force = false): Promise<void> {
  const value = row.studentId.trim()
  if (!value) {
    invalidateMatch(row)
    return Promise.resolve()
  }
  if (!force && row.oid) return Promise.resolve()
  if (row.pending && row.pendingValue === value) return row.pending

  const token = row.seq + 1
  row.seq = token
  row.oid = ''
  row.name = ''
  row.error = ''
  row.checking = true
  row.pendingValue = value

  const task = (async () => {
    try {
      const result = await lookupStudent(value)
      if (row.seq !== token) return
      if (result.status === 'matched') {
        row.oid = result.student._id
        row.name = result.student.name
        row.studentId = String(result.student.id)
      } else if (result.status === 'ambiguous') {
        row.error = `匹配到多名成员（${result.options.join('、')}），请改填学号`
      } else {
        row.error = '未找到该学号或姓名'
      }
    } catch (reason) {
      if (row.seq !== token) return
      row.error = readableError(reason, '成员查询失败，请检查网络后重试')
    } finally {
      if (row.seq === token) {
        row.checking = false
        row.pending = null
        row.pendingValue = ''
      }
    }
  })()

  row.pending = task
  return task
}

// 一次提交最多 100 行；同时打 100 个查询会把浏览器连接池和后台读限速一起打满，
// 所以按 4 个并发串起来跑，行的顺序仍然是表单顺序。
const LOOKUP_CONCURRENCY = 4

async function resolveRows(rows: AddRow[]): Promise<void> {
  const queue = rows.slice()
  const workers = Array.from(
    { length: Math.min(LOOKUP_CONCURRENCY, queue.length) },
    async () => {
      for (;;) {
        const row = queue.shift()
        if (!row) return
        await resolveRow(row)
      }
    }
  )
  await Promise.all(workers)
}

function addRow() {
  addRows.value.push(createAddRow())
}

function removeAddRow(index: number) {
  addRows.value.splice(index, 1)
  if (!addRows.value.length) addRows.value.push(createAddRow())
}

function durationValue(value: string | number): number | null {
  // Vue 的 v-model 在 `<input type="number">` 上会把值转成 number；这里统一
  // 转回文本再判空，否则 `value.trim()` 会在真实浏览器里抛 TypeError，
  // 表现就是「填了时长但一点提交就失败」。
  const text = String(value ?? '').trim()
  const number = Number(text)
  // 空串会被 Number() 变成 0，这里必须先按文本挡掉，否则「留空」会被当成 0。
  if (!text || !Number.isFinite(number) || number <= 0 || number > 24) return null
  // 不四舍五入：`duration_hours` 是 BINARY_DOUBLE，静默改掉用户填的 1.25 只会
  // 让「保存后和输入不一致」，也会让未改动的行被误判成有修改。
  return number
}

function populatedAddRows(): AddRow[] {
  return addRows.value.filter(
    (row) => row.studentId.trim() || String(row.duration ?? '').trim()
  )
}

function validateAddRows(): string {
  const populated = populatedAddRows()
  if (!populated.length) return '至少填写一名成员。'
  if (populated.length > 100) return '一次最多登记 100 名成员。'
  if (!selectedActivity.value) return '请先选择活动。'
  const seen = new Map<string, string>()
  for (const row of populated) {
    const value = row.studentId.trim()
    if (!value) return '请填写成员学号。'
    if (!row.oid) {
      // 匹配失败（查无此人 / 同名 / 网络错误）各有各的提示，不要统一成「尚未匹配」。
      return row.error
        ? `${value}：${row.error}`
        : `学号 ${value} 尚未匹配到成员，请稍候或重新填写。`
    }
    if (durationValue(row.duration) === null) {
      return `学号 ${value} 的时长需大于 0 且不超过 24 小时。`
    }
    // 后端按 (member, mode) 聚合：同一个人同一模式的重复行会被静默累加，
    // 所以这里先拦住；不同模式是两条独立记录，允许分别提交。
    const key = `${row.oid}:${row.mode}`
    const duplicate = seen.get(key)
    if (duplicate) {
      return `${row.name || value} 在本次提交中重复（${MODE_LABELS[row.mode]}），请合并后再提交。`
    }
    seen.set(key, value)
  }
  return ''
}

async function submitAdd() {
  if (submitting.value) return
  const activity = selectedActivity.value
  if (!activity) {
    setFeedback('请先选择活动。', 'error')
    return
  }
  const populated = populatedAddRows()
  if (!populated.length) {
    setFeedback('至少填写一名成员。', 'error')
    return
  }
  submitting.value = true
  feedback.value = ''
  try {
    // 点击「添加成员」时输入框先 blur、查询才刚发出，`row.oid` 还是空的：
    // 以前这里直接校验，于是「明明填了学号」却提示尚未匹配。现在先等所有
    // 已填行解析完（复用 blur 已在跑的请求），再统一校验。
    await resolveRows(populated)
    const error = validateAddRows()
    if (error) {
      setFeedback(error, 'error')
      return
    }
    await addActivityMembers(
      activity._id,
      populated.map((row) => ({
        member: row.oid,
        mode: row.mode,
        duration: durationValue(row.duration) ?? 0
      }))
    )
    setFeedback(`已添加 ${populated.length} 名成员到「${activity.name}」。`, 'success')
    resetAddRows()
  } catch (reason) {
    const message = readableError(reason, '添加成员失败，请检查网络后重试。')
    setFeedback(
      reason instanceof ApiError && reason.status === 409
        ? `${message}（本页不会覆盖已有记录，请先移除这些成员再提交。）`
        : message,
      'error'
    )
  } finally {
    submitting.value = false
  }
}

interface EditChange {
  row: EditRow
  duration: number
}

/**
 * 收集真正需要提交的改动。
 *
 * 只把「合法且与原值不同」的行交出去；同时把时长非法但用户确实动过的行拦成
 * 明确错误——旧实现里这类行被静默跳过，界面照样提示「已保存」，用户以为改了。
 * 历史数据里的 0/空时长只要没动过就不参与判断，免得挡住其他行的保存。
 */
function collectEditChanges(): { changes: EditChange[]; error: string } {
  const changes: EditChange[] = []
  for (const row of editRows.value) {
    const duration = durationValue(row.duration)
    const raw = String(row.duration ?? '').trim()
    const label = row.name || row.studentId || '该成员'
    if (duration === null) {
      if (
        row.mode !== row.originalMode ||
        raw !== row.originalDurationText.trim()
      ) {
        return { changes: [], error: `成员 ${label} 的时长需大于 0 且不超过 24 小时。` }
      }
      continue
    }
    if (duration !== row.originalDuration || row.mode !== row.originalMode) {
      changes.push({ row, duration })
    }
  }
  return { changes, error: '' }
}

async function submitModify() {
  if (submitting.value) return
  const activity = selectedActivity.value
  if (!activity) {
    setFeedback('请先选择活动。', 'error')
    return
  }
  if (!editRows.value.length) {
    setFeedback('这个活动还没有录入成员。', 'error')
    return
  }
  const { changes, error } = collectEditChanges()
  if (error) {
    setFeedback(error, 'error')
    return
  }
  if (!changes.length) {
    setFeedback('没有需要保存的修改。', 'error')
    return
  }

  submitting.value = true
  feedback.value = ''
  let saved = 0
  const failures: string[] = []
  try {
    for (const { row, duration } of changes) {
      row.saving = true
      row.message = ''
      try {
        await updateActivityMember(activity._id, row.memberId, duration, row.mode)
        row.originalDuration = duration
        row.originalDurationText = String(duration)
        row.originalMode = row.mode
        row.message = '已保存'
        saved += 1
      } catch (reason) {
        row.message = readableError(reason, '保存失败')
        failures.push(`${row.name || row.studentId || row.memberId}：${row.message}`)
      } finally {
        row.saving = false
      }
    }
  } finally {
    submitting.value = false
  }
  setFeedback(
    failures.length
      ? `已保存 ${saved} 条，${failures.length} 条失败：${failures.join('；')}`
      : `已保存 ${saved} 条成员记录。`,
    failures.length ? 'error' : 'success'
  )
}

function goBack() {
  router.back()
}

onMounted(() => {
  resetAddRows()
  void loadActivities()
})

// 添加/修改共用同一个组件；从 /add 切到 /modify 时组件不会重新挂载。
// 不监听 mode 的话标题会变成「修改义工」，但成员列表仍是空的，用户会以为
// 「没有成员 / 无法提交」。切模式时清掉上一模式的临时状态，并按当前活动
// 重新加载成员（或重置添加行）。
watch(mode, () => {
  feedback.value = ''
  editRows.value = []
  membersError.value = ''
  resetAddRows()
  if (selectedActivityId.value) void handleActivityChange()
  else void loadActivities()
})
</script>

<template>
  <div class="register-page">
    <div class="register-page__container">
      <header class="register-page__header surface-enter" style="--i: 0">
        <button type="button" class="register-page__back" aria-label="返回" @click="goBack">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M12 4L6 10L12 16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <div>
          <p class="register-page__eyebrow">义工登记</p>
          <h1 class="register-page__title">{{ pageTitle }}</h1>
          <p class="register-page__subtitle">{{ pageSubtitle }}</p>
        </div>
      </header>

      <section class="picker-card surface-enter" style="--i: 1">
        <div class="field">
          <label class="field__label" for="activity-search">搜索活动</label>
          <div class="field__inline">
            <input
              id="activity-search"
              v-model="activityQuery"
              class="field__input"
              type="search"
              placeholder="输入活动名称或地点"
              maxlength="64"
              @keydown.enter.prevent="loadActivities"
            />
            <button class="glass-action" type="button" :disabled="activitiesLoading" @click="loadActivities">
              {{ activitiesLoading ? '搜索中…' : '搜索' }}
            </button>
          </div>
        </div>
        <div class="field">
          <label class="field__label" for="activity-select">选择活动</label>
          <select
            id="activity-select"
            v-model="selectedActivityId"
            class="field__input field__select"
            :disabled="activitiesLoading"
            @change="handleActivityChange"
          >
            <option value="">请选择活动</option>
            <option v-for="activity in activities" :key="activity._id" :value="activity._id">
              {{ activity.name }}<template v-if="formatActivityMeta(activity)"> · {{ formatActivityMeta(activity) }}</template>
            </option>
          </select>
        </div>
        <BubbleFeedback v-if="activitiesError" class="glass-feedback--error" role="alert">{{ activitiesError }}</BubbleFeedback>
        <BubbleFeedback v-else-if="!activitiesLoading && !activities.length">
          没有找到可登记的活动。请先在「创建义工」中新建活动。
        </BubbleFeedback>
        <div v-if="selectedActivity" class="activity-summary">
          <span class="activity-summary__name">{{ selectedActivity.name }}</span>
          <span class="activity-summary__meta">{{ formatActivityMeta(selectedActivity) || '暂无时间地点' }}</span>
        </div>
      </section>

      <template v-if="selectedActivity">
        <section v-if="mode === 'add'" class="work-card surface-enter" style="--i: 2">
          <div class="work-card__head">
            <div>
              <h2 class="work-card__title">待添加成员</h2>
              <p class="work-card__hint">输入学号或姓名后离开输入框即可匹配成员。</p>
            </div>
            <button class="glass-action" type="button" @click="addRow">+ 添加一行</button>
          </div>

          <div class="member-rows">
            <article v-for="(row, index) in addRows" :key="row.key" class="member-row">
              <div class="member-row__grid">
                <div class="field">
                  <label class="field__label">学号 / 姓名</label>
                  <input
                    v-model="row.studentId"
                    class="field__input"
                    type="text"
                    placeholder="例如 2026001"
                    :disabled="submitting"
                    @input="onStudentInput(row)"
                    @blur="resolveRow(row)"
                  />
                </div>
                <div class="field">
                  <label class="field__label">成员</label>
                  <input class="field__input" :value="row.checking ? '匹配中…' : row.name" readonly placeholder="自动匹配" />
                </div>
                <div class="field">
                  <label class="field__label">时长（小时）</label>
                  <input
                    v-model="row.duration"
                    class="field__input"
                    type="number"
                    min="0.1"
                    max="24"
                    step="0.5"
                    placeholder="2"
                    :disabled="submitting"
                  />
                </div>
                <div class="field">
                  <label class="field__label">类型</label>
                  <select v-model="row.mode" class="field__input field__select" :disabled="modeOptions.length === 1 || submitting">
                    <option v-for="option in modeOptions" :key="option" :value="option">{{ MODE_LABELS[option] }}</option>
                  </select>
                </div>
              </div>
              <BubbleFeedback v-if="row.error" class="glass-feedback--error" role="alert">{{ row.error }}</BubbleFeedback>
              <button class="remove-row" type="button" :disabled="submitting" @click="removeAddRow(index)">移除</button>
            </article>
          </div>

          <div class="work-card__actions">
            <button class="primary-action" type="button" :disabled="submitting" @click="submitAdd">
              {{ submitting ? '提交中…' : '添加成员' }}
            </button>
            <button class="glass-action" type="button" :disabled="submitting" @click="goBack">取消</button>
          </div>
        </section>

        <section v-else class="work-card surface-enter" style="--i: 2">
          <div class="work-card__head">
            <div>
              <h2 class="work-card__title">已录入成员</h2>
              <p class="work-card__hint">修改时长或类型后保存；只覆盖对应成员记录。</p>
            </div>
            <button class="glass-action" type="button" :disabled="membersLoading" @click="loadMembers">
              {{ membersLoading ? '刷新中…' : '刷新成员' }}
            </button>
          </div>

          <BubbleFeedback v-if="membersError" class="glass-feedback--error" role="alert">{{ membersError }}</BubbleFeedback>
          <p v-else-if="membersLoading" class="message">正在加载成员…</p>
           <BubbleFeedback v-else-if="!editRows.length">这个活动还没有录入成员。</BubbleFeedback>

          <div v-else class="member-rows">
            <article v-for="row in editRows" :key="row.key" class="member-row">
              <div class="member-row__grid">
                <div class="field">
                  <label class="field__label">成员</label>
                  <input class="field__input" :value="row.name" readonly />
                </div>
                <div class="field">
                  <label class="field__label">学号</label>
                  <input class="field__input" :value="row.studentId || '—'" readonly />
                </div>
                <div class="field">
                  <label class="field__label">时长（小时）</label>
                  <input
                    v-model="row.duration"
                    class="field__input"
                    type="number"
                    min="0.1"
                    max="24"
                    step="0.5"
                    :disabled="row.saving || submitting"
                  />
                </div>
                <div class="field">
                  <label class="field__label">类型</label>
                  <select v-model="row.mode" class="field__input field__select" :disabled="editModeOptions(row).length === 1 || row.saving || submitting">
                    <option v-for="option in editModeOptions(row)" :key="option" :value="option">{{ MODE_LABELS[option] }}</option>
                  </select>
                </div>
              </div>
              <BubbleFeedback v-if="row.message" :key="row.message" :class="row.message === '已保存' ? 'glass-feedback--success' : 'glass-feedback--error'" role="status">
                {{ row.message }}
              </BubbleFeedback>
            </article>
          </div>

          <BubbleFeedback v-if="membersTruncated" class="glass-feedback--warn" role="status">
            成员较多，这里仅显示前 100 条记录（共 {{ membersTotal }} 条）；其余记录请在主站处理。
          </BubbleFeedback>

          <div class="work-card__actions">
            <button class="primary-action" type="button" :disabled="submitting || membersLoading" @click="submitModify">
              {{ submitting ? '保存中…' : '保存修改' }}
            </button>
            <button class="glass-action" type="button" :disabled="submitting" @click="goBack">取消</button>
          </div>
        </section>
      </template>

      <BubbleFeedback v-if="feedback" :key="feedback" :class="`glass-feedback--${feedbackType}`" :role="feedbackType === 'error' ? 'alert' : 'status'">
        {{ feedback }}
      </BubbleFeedback>
    </div>
  </div>
</template>

<style scoped>
.register-page {
  min-height: 100vh;
  padding: var(--space-7) var(--content-padding) var(--space-10);
}

.register-page__container {
  max-width: var(--content-max-width);
  margin: 0 auto;
  display: grid;
  gap: var(--space-6);
}

.register-page__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
}

.register-page__back {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.84);
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.8), rgba(232, 242, 252, 0.56));
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  backdrop-filter: blur(12px) saturate(180%);
  color: var(--color-text-secondary);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    0 2px 8px rgba(18, 48, 82, 0.08);
  cursor: pointer;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    color var(--duration-fast) var(--ease-smooth);
}

.register-page__back:hover { color: var(--color-text-primary); }
.register-page__back:active { transform: scale(0.94); }
.register-page__back:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }

.register-page__eyebrow {
  margin: 0 0 var(--space-1);
  color: var(--color-text-tertiary);
  font-size: var(--font-size-caption);
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.register-page__title {
  margin: 0;
  font-size: var(--font-size-title-2);
  font-weight: 650;
  line-height: var(--line-height-tight);
  letter-spacing: var(--letter-spacing-tight);
}

.register-page__subtitle {
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  line-height: var(--line-height-relaxed);
}

.picker-card,
.work-card {
  position: relative;
  background: var(--surface-matte);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  padding: var(--space-6);
}

.picker-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: var(--space-5);
  align-items: end;
}

.picker-card > .message,
.picker-card > .activity-summary {
  grid-column: 1 / -1;
}

.field {
  display: grid;
  gap: var(--space-2);
  min-width: 0;
}

.field__label {
  color: var(--color-text-secondary);
  font-size: var(--font-size-callout);
  font-weight: 600;
}

.field__inline {
  display: flex;
  gap: var(--space-2);
}

.field__input {
  width: 100%;
  min-height: 42px;
  padding: 0 var(--space-3);
  color: var(--color-text-primary);
  background: rgba(255, 255, 255, 0.56);
  border: 1px solid rgba(255, 255, 255, 0.76);
  border-radius: var(--radius-md);
  outline: none;
  font: inherit;
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.field__input:focus {
  border-color: rgba(0, 113, 227, 0.44);
  box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
}

.field__input[readonly] {
  color: var(--color-text-secondary);
  background: rgba(255, 255, 255, 0.3);
}

.field__select {
  appearance: none;
  background-image: linear-gradient(45deg, transparent 50%, var(--color-text-tertiary) 50%),
    linear-gradient(135deg, var(--color-text-tertiary) 50%, transparent 50%);
  background-position: calc(100% - 18px) 18px, calc(100% - 13px) 18px;
  background-size: 5px 5px, 5px 5px;
  background-repeat: no-repeat;
  padding-right: 34px;
}

.glass-action,
.primary-action {
  min-height: 40px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-pill);
  font: inherit;
  font-size: var(--font-size-callout);
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition:
    transform var(--duration-fast) var(--ease-spring),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.glass-action {
  color: var(--color-text-secondary);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.78), rgba(232, 242, 252, 0.54));
  border: 1px solid rgba(255, 255, 255, 0.86);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.primary-action {
  color: #fff;
  background: linear-gradient(135deg, #0a84ff, #0071e3);
  border: 1px solid rgba(0, 113, 227, 0.34);
  box-shadow: 0 6px 16px rgba(0, 113, 227, 0.22);
}

.glass-action:hover,
.primary-action:hover { transform: translateY(-1px); }
.glass-action:active,
.primary-action:active { transform: scale(0.98); }
.glass-action:disabled,
.primary-action:disabled { opacity: 0.55; cursor: not-allowed; transform: none; }
.glass-action:focus-visible,
.primary-action:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }

.activity-summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
  align-items: baseline;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(0, 113, 227, 0.07);
  border: 1px solid rgba(0, 113, 227, 0.1);
}

.activity-summary__name { color: var(--color-text-primary); font-weight: 650; }
.activity-summary__meta { color: var(--color-text-secondary); font-size: var(--font-size-callout); }

.work-card { display: grid; gap: var(--space-5); }

.work-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.work-card__title { margin: 0; font-size: var(--font-size-title-3); font-weight: 650; }
.work-card__hint { margin: var(--space-1) 0 0; color: var(--color-text-tertiary); font-size: var(--font-size-callout); }

.member-rows { display: grid; gap: var(--space-4); }

.member-row {
  position: relative;
  padding: var(--space-4);
  border: 1px solid rgba(255, 255, 255, 0.68);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.36);
  transition:
    border-color var(--duration-fast) var(--ease-smooth),
    background var(--duration-fast) var(--ease-smooth),
    box-shadow var(--duration-fast) var(--ease-smooth);
}

.member-row:hover {
  border-color: rgba(255, 255, 255, 0.84);
  background: rgba(255, 255, 255, 0.46);
  box-shadow: 0 2px 8px rgba(18, 48, 82, 0.06);
}

.member-row__grid {
  display: grid;
  grid-template-columns: 1.2fr 1.2fr 0.8fr 0.9fr;
  gap: var(--space-4);
}

.remove-row {
  position: absolute;
  right: var(--space-3);
  top: var(--space-3);
  border: 0;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-caption);
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-smooth);
}

.remove-row:hover { color: var(--tone-rose); }
.remove-row:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.work-card__actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }

.message {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
}

@media (max-width: 820px) {
  .picker-card { grid-template-columns: 1fr; }
  .member-row__grid { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 560px) {
  .register-page { padding: var(--space-5) var(--space-4) var(--space-8); }
  .register-page__title { font-size: var(--font-size-title-3); }
  .member-row__grid { grid-template-columns: 1fr; }
  .work-card__head { flex-direction: column; }
}

@media (prefers-reduced-motion: reduce) {
  .register-page__back,
  .glass-action,
  .primary-action,
  .member-row,
  .remove-row,
  .field__input,
  .feedback-enter-active,
  .feedback-leave-active { transition: none; }
}
</style>
