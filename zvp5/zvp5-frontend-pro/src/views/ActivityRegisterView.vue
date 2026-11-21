<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
  RegistrationMode,
  StudentRecord
} from '@/api/activity-admin'
import { readableError } from '@/api/http'

interface AddRow {
  key: string
  studentId: string
  name: string
  oid: string
  duration: string
  mode: RegistrationMode
  checking: boolean
  error: string
}

interface EditRow {
  key: string
  memberId: string
  memberOid: string
  studentId: string
  name: string
  duration: string
  originalDuration: number
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
    error: ''
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
    const result = await listActivityMembers(selectedActivity.value._id, '', 1, 200)
    editRows.value = result.members.map((member: ActivityMemberRow) => ({
      key: nextKey(),
      memberId: member._id,
      memberOid: member.member,
      studentId: String(member.user?.id ?? ''),
      name: member.member_name || member.user?.name || '未命名成员',
      duration: String(member.duration ?? ''),
      originalDuration: Number(member.duration) || 0,
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

async function resolveRow(row: AddRow) {
  const value = row.studentId.trim()
  if (!value) {
    row.oid = ''
    row.name = ''
    row.error = ''
    return
  }
  row.checking = true
  row.error = ''
  try {
    const student: StudentRecord | null = await lookupStudent(value)
    if (!student) {
      row.oid = ''
      row.name = ''
      row.error = '未找到该学号或姓名'
      return
    }
    row.oid = student._id
    row.name = student.name
    row.studentId = String(student.id)
  } catch (reason) {
    row.oid = ''
    row.error = readableError(reason, '成员查询失败')
  } finally {
    row.checking = false
  }
}

function addRow() {
  addRows.value.push(createAddRow())
}

function removeAddRow(index: number) {
  addRows.value.splice(index, 1)
  if (!addRows.value.length) addRows.value.push(createAddRow())
}

function durationValue(value: string): number | null {
  const number = Number(value)
  if (!Number.isFinite(number) || number <= 0 || number > 24) return null
  return Math.round(number * 10) / 10
}

function validateAddRows(): string {
  const populated = addRows.value.filter(
    (row) => row.studentId.trim() || row.duration.trim()
  )
  if (!populated.length) return '至少填写一名成员。'
  for (const row of populated) {
    if (!row.studentId.trim()) return '请填写成员学号。'
    if (!row.oid) return `学号 ${row.studentId} 尚未匹配到成员。`
    if (durationValue(row.duration) === null) return `学号 ${row.studentId} 的时长需在 0–24 之间。`
  }
  if (!selectedActivity.value) return '请先选择活动。'
  return ''
}

async function submitAdd() {
  if (submitting.value) return
  const error = validateAddRows()
  if (error) {
    setFeedback(error, 'error')
    return
  }
  const activity = selectedActivity.value
  if (!activity) return
  const rows = addRows.value.filter((row) => row.oid && row.duration.trim())
  submitting.value = true
  feedback.value = ''
  try {
    await addActivityMembers(
      activity._id,
      rows.map((row) => ({
        member: row.oid,
        mode: row.mode,
        duration: durationValue(row.duration) ?? 0
      }))
    )
    setFeedback(`已添加 ${rows.length} 名成员到「${activity.name}」。`, 'success')
    resetAddRows()
  } catch (reason) {
    setFeedback(readableError(reason, '添加成员失败，请检查是否已经录入。'), 'error')
  } finally {
    submitting.value = false
  }
}

function changedEditRows(): EditRow[] {
  return editRows.value.filter((row) => {
    const duration = durationValue(row.duration)
    return duration !== null && (duration !== row.originalDuration || row.mode !== row.originalMode)
  })
}

async function submitModify() {
  if (submitting.value) return
  const activity = selectedActivity.value
  if (!activity) {
    setFeedback('请先选择活动。', 'error')
    return
  }
  const rows = changedEditRows()
  if (!rows.length) {
    setFeedback('没有需要保存的修改。', 'error')
    return
  }
  const invalid = editRows.value.find(
    (row) => row.duration.trim() && durationValue(row.duration) === null
  )
  if (invalid) {
    setFeedback(`成员 ${invalid.name} 的时长需在 0–24 之间。`, 'error')
    return
  }

  submitting.value = true
  feedback.value = ''
  let saved = 0
  const failures: string[] = []
  for (const row of rows) {
    row.saving = true
    row.message = ''
    try {
      const duration = durationValue(row.duration) ?? 0
      await updateActivityMember(activity._id, row.memberId, duration, row.mode)
      row.originalDuration = duration
      row.originalMode = row.mode
      row.message = '已保存'
      saved += 1
    } catch (reason) {
      row.message = readableError(reason, '保存失败')
      failures.push(row.name)
    } finally {
      row.saving = false
    }
  }
  setFeedback(
    failures.length
      ? `已保存 ${saved} 条，${failures.length} 条失败：${failures.join('、')}`
      : `已保存 ${saved} 条成员记录。`,
    failures.length ? 'error' : 'success'
  )
  submitting.value = false
}

function goBack() {
  router.back()
}

onMounted(() => {
  resetAddRows()
  void loadActivities()
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
        <p v-if="activitiesError" class="message message--error" role="alert">{{ activitiesError }}</p>
        <p v-else-if="!activitiesLoading && !activities.length" class="message">
          没有找到可登记的活动。请先在「创建义工」中新建活动。
        </p>
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
              <p v-if="row.error" class="message message--error" role="alert">{{ row.error }}</p>
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

          <p v-if="membersError" class="message message--error" role="alert">{{ membersError }}</p>
          <p v-else-if="membersLoading" class="message">正在加载成员…</p>
          <p v-else-if="!editRows.length" class="message">这个活动还没有录入成员。</p>

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
                  <select v-model="row.mode" class="field__input field__select" :disabled="modeOptions.length === 1 || row.saving || submitting">
                    <option v-for="option in modeOptions" :key="option" :value="option">{{ MODE_LABELS[option] }}</option>
                  </select>
                </div>
              </div>
              <p v-if="row.message" class="message" :class="{ 'message--error': row.message !== '已保存' }">
                {{ row.message }}
              </p>
            </article>
          </div>

          <div class="work-card__actions">
            <button class="primary-action" type="button" :disabled="submitting || membersLoading" @click="submitModify">
              {{ submitting ? '保存中…' : '保存修改' }}
            </button>
            <button class="glass-action" type="button" :disabled="submitting" @click="goBack">取消</button>
          </div>
        </section>
      </template>

      <p v-if="feedback" class="glass-feedback" :class="`glass-feedback--${feedbackType}`" role="status">
        {{ feedback }}
      </p>
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
  gap: var(--space-5);
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
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.register-page__title {
  margin: 0;
  font-size: var(--font-size-title-2);
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
  padding: var(--space-5);
}

.picker-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: var(--space-4);
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
  font-size: var(--font-size-caption);
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

.work-card { display: grid; gap: var(--space-4); }

.work-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.work-card__title { margin: 0; font-size: var(--font-size-title-3); }
.work-card__hint { margin: var(--space-1) 0 0; color: var(--color-text-tertiary); font-size: var(--font-size-callout); }

.member-rows { display: grid; gap: var(--space-3); }

.member-row {
  position: relative;
  padding: var(--space-4);
  border: 1px solid rgba(255, 255, 255, 0.68);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.36);
}

.member-row__grid {
  display: grid;
  grid-template-columns: 1.2fr 1.2fr 0.8fr 0.9fr;
  gap: var(--space-3);
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
}

.remove-row:hover { color: var(--tone-rose); }

.work-card__actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }

.message {
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-callout);
  line-height: var(--line-height-normal);
}

.message--error { color: var(--tone-rose); }

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
  .glass-action,
  .primary-action,
  .feedback-enter-active,
  .feedback-leave-active { transition: none; }
}
</style>
