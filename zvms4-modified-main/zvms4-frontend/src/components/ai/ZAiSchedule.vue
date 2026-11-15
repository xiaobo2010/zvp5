<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUserStore } from '@/stores/user'
import { errorMessage } from '@/utils/errorMessage'
import StateBlock from '@/ui/ux/StateBlock.vue'
import Skeleton from '@/ui/ux/Skeleton.vue'
import {
  AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES,
  AI_SCHEDULE_MIN_INTERVAL_MINUTES,
  AI_SCHEDULE_PROMPT_MAX,
  createAiScheduledTask,
  deleteAiScheduledTask,
  isValidCronExpression,
  listAiScheduledTaskRuns,
  listAiScheduledTasks,
  reviewAiScheduledTask,
  runAiScheduledTaskNow,
  updateAiScheduledTask
} from '@/api/next/ai/schedule'
import type {
  AiScheduledReviewStatus,
  AiScheduledTask,
  AiScheduledTaskRun
} from '@/api/next/ai-types'

const { t } = useI18n()
const user = useUserStore()
const isAdmin = computed(() => user.position.includes('admin'))

const rows = ref<AiScheduledTask[]>([])
const loading = ref(true)
const failed = ref('')
const status = ref<AiScheduledReviewStatus | ''>('')
const allTasks = ref(false)
const busy = ref('')

const form = ref({
  name: '',
  prompt_text: '',
  kind: 'interval' as 'interval' | 'cron',
  interval_minutes: AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES,
  cron_expr: ''
})
const createError = ref('')

const runs = ref<{ task: AiScheduledTask; rows: AiScheduledTaskRun[] } | null>(null)

async function load() {
  loading.value = true
  failed.value = ''
  try {
    const page = await listAiScheduledTasks({
      allTasks: isAdmin.value && allTasks.value,
      reviewStatus: status.value || undefined,
      limit: 50
    })
    rows.value = page.rows
  } catch (error) {
    failed.value = errorMessage(error, '加载定时任务失败')
    rows.value = []
  } finally {
    loading.value = false
  }
}

async function submit() {
  createError.value = ''
  const name = form.value.name.trim()
  const prompt = form.value.prompt_text.trim()
  if (!name || !prompt) {
    createError.value = '名称与提示词都要填'
    return
  }
  if (prompt.length > AI_SCHEDULE_PROMPT_MAX) {
    createError.value = `提示词最多 ${AI_SCHEDULE_PROMPT_MAX} 字`
    return
  }
  if (form.value.kind === 'cron' && !isValidCronExpression(form.value.cron_expr)) {
    createError.value = 'cron 需要 5 段，例如 30 8 * * 1'
    return
  }
  const interval = Math.max(AI_SCHEDULE_MIN_INTERVAL_MINUTES, Number(form.value.interval_minutes) || 0)
  busy.value = 'create'
  try {
    await createAiScheduledTask({
      name,
      prompt_text: prompt,
      schedule_kind: form.value.kind,
      interval_minutes: interval,
      cron_expr: form.value.kind === 'cron' ? form.value.cron_expr.trim() : null
    })
    form.value = {
      name: '',
      prompt_text: '',
      kind: 'interval',
      interval_minutes: AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES,
      cron_expr: ''
    }
    await load()
  } catch (error) {
    createError.value = errorMessage(error, '创建失败')
  } finally {
    busy.value = ''
  }
}

async function toggle(task: AiScheduledTask) {
  busy.value = task.oid
  try {
    await updateAiScheduledTask(task.oid, { enabled: !task.enabled })
    await load()
  } catch (error) {
    failed.value = errorMessage(error, '更新失败')
  } finally {
    busy.value = ''
  }
}

async function runNow(task: AiScheduledTask) {
  busy.value = task.oid
  try {
    await runAiScheduledTaskNow(task.oid)
    await load()
  } catch (error) {
    failed.value = errorMessage(error, '立即执行失败')
  } finally {
    busy.value = ''
  }
}

async function review(task: AiScheduledTask, decision: 'approved' | 'rejected') {
  busy.value = task.oid
  try {
    await reviewAiScheduledTask(task.oid, decision)
    await load()
  } catch (error) {
    failed.value = errorMessage(error, '审核失败')
  } finally {
    busy.value = ''
  }
}

async function remove(task: AiScheduledTask) {
  busy.value = task.oid
  try {
    await deleteAiScheduledTask(task.oid)
    await load()
  } catch (error) {
    failed.value = errorMessage(error, '删除失败')
  } finally {
    busy.value = ''
  }
}

async function openRuns(task: AiScheduledTask) {
  busy.value = task.oid
  try {
    const page = await listAiScheduledTaskRuns(task.oid, 1, 20)
    runs.value = { task, rows: page.rows }
  } catch (error) {
    failed.value = errorMessage(error, '读取执行历史失败')
  } finally {
    busy.value = ''
  }
}

const statusLabel: Record<string, string> = {
  draft: '草稿',
  pending: '待审',
  approved: '已通过',
  rejected: '已驳回'
}

function scheduleText(task: AiScheduledTask) {
  return task.schedule_kind === 'cron'
    ? `cron ${task.cron_expr ?? '—'}`
    : `每 ${task.interval_minutes} 分钟`
}

function timeText(value: string | null) {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

onMounted(load)
</script>

<template>
  <div class="sch">
    <section class="yg2-card sch__card">
      <header class="sch__head">
        <div>
          <h2 class="yg2-card__title">定时任务</h2>
          <p class="sch__hint">
            新建的任务先进入待审；管理员通过后才会按计划执行。改提示词会被打回草稿重新审。
          </p>
        </div>
        <div class="sch__filters">
          <select v-if="isAdmin" v-model="status" class="yg2-input sch__select" @change="load">
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="pending">待审</option>
            <option value="approved">已通过</option>
            <option value="rejected">已驳回</option>
          </select>
          <label v-if="isAdmin" class="sch__check">
            <input v-model="allTasks" type="checkbox" @change="load" />
            含他人的任务
          </label>
          <button class="yg2-btn yg2-btn--secondary yg2-btn--sm" type="button" @click="load">
            刷新
          </button>
        </div>
      </header>

      <p v-if="failed" class="sch__error">{{ failed }}</p>

      <div v-if="loading" class="sch__loading">
        <Skeleton :lines="3" height="16px" />
      </div>
      <StateBlock
        v-else-if="!rows.length"
        title="还没有定时任务"
        description="在下面填一条提示词，选好频率，提交后等管理员通过即可按计划执行。"
      />
      <ul v-else class="sch__list">
        <li v-for="task in rows" :key="task.oid" class="sch__item">
          <div class="sch__main">
            <div class="sch__title-row">
              <strong>{{ task.name }}</strong>
              <span class="sch__badge" :class="`is-${task.review_status}`">
                {{ statusLabel[task.review_status] || task.review_status }}
              </span>
              <span v-if="!task.enabled" class="sch__badge is-off">已停用</span>
            </div>
            <p class="sch__meta">
              {{ scheduleText(task) }} · 下次 {{ timeText(task.next_run_at) }} · 已执行
              {{ task.run_count }} 次
            </p>
            <p v-if="task.last_error" class="sch__err">{{ task.last_error }}</p>
          </div>
          <div class="sch__actions">
            <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" @click="toggle(task)">
              {{ task.enabled ? '停用' : '启用' }}
            </button>
            <button
              class="yg2-btn yg2-btn--quiet yg2-btn--sm"
              type="button"
              :disabled="task.review_status !== 'approved' || busy === task.oid"
              @click="runNow(task)"
            >
              立即执行
            </button>
            <button class="yg2-btn yg2-btn--quiet yg2-btn--sm" type="button" @click="openRuns(task)">
              历史
            </button>
            <template v-if="isAdmin && task.review_status === 'pending'">
              <button class="yg2-btn yg2-btn--sm" type="button" @click="review(task, 'approved')">
                通过
              </button>
              <button class="yg2-btn yg2-btn--sm yg2-btn--danger" type="button" @click="review(task, 'rejected')">
                驳回
              </button>
            </template>
            <button class="yg2-btn yg2-btn--sm yg2-btn--danger" type="button" @click="remove(task)">
              删除
            </button>
          </div>
        </li>
      </ul>
    </section>

    <section class="yg2-card sch__card">
      <h2 class="yg2-card__title">新建任务</h2>
      <div class="sch__form">
        <label class="sch__field">
          <span>名称</span>
          <input v-model="form.name" class="yg2-input" maxlength="128" placeholder="如：每周提醒未报名同学" />
        </label>
        <label class="sch__field">
          <span>频率</span>
          <select v-model="form.kind" class="yg2-input">
            <option value="interval">固定间隔</option>
            <option value="cron">cron 表达式</option>
          </select>
        </label>
        <label v-if="form.kind === 'interval'" class="sch__field">
          <span>间隔（分钟，最少 15）</span>
          <input v-model.number="form.interval_minutes" class="yg2-input" type="number" min="15" />
        </label>
        <label v-else class="sch__field">
          <span>cron（5 段，如 30 8 * * 1）</span>
          <input v-model="form.cron_expr" class="yg2-input" placeholder="30 8 * * 1" />
        </label>
        <label class="sch__field sch__field--wide">
          <span>提示词（{{ form.prompt_text.length }} / {{ AI_SCHEDULE_PROMPT_MAX }}）</span>
          <textarea
            v-model="form.prompt_text"
            class="yg2-input sch__textarea"
            rows="4"
            placeholder="到点后要交给 AI 执行的那段话"
          />
        </label>
      </div>
      <p v-if="createError" class="sch__error">{{ createError }}</p>
      <div class="sch__submit">
        <button class="yg2-btn yg2-btn--primary" type="button" :disabled="busy === 'create'" @click="submit">
          {{ busy === 'create' ? '提交中…' : '提交待审' }}
        </button>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="runs" class="sch__mask" @click.self="runs = null">
        <div class="sch__modal">
          <header class="sch__modal-head">
            <strong>{{ runs.task.name }} · 执行历史</strong>
            <button class="yg2-icon-btn" type="button" aria-label="关闭" @click="runs = null">✕</button>
          </header>
          <p v-if="!runs.rows.length" class="sch__hint">还没有执行记录。</p>
          <ul v-else class="sch__runs">
            <li v-for="run in runs.rows" :key="run.run_id">
              <span class="sch__run-time">{{ timeText(run.started_at) }}</span>
              <span class="sch__badge" :class="`is-${run.status === 'ok' ? 'approved' : 'rejected'}`">
                {{ run.status }}
              </span>
              <span class="sch__run-text">{{ run.error_message || run.stop_reason || run.error_code || '—' }}</span>
            </li>
          </ul>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.sch {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sch__card {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sch__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.sch__hint {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.sch__filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.sch__select {
  width: 140px;
  height: 34px;
}

.sch__check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--yg2-text-secondary);
}

.sch__error,
.sch__err {
  margin: 0;
  font-size: 12.5px;
  color: var(--yg2-danger);
}

.sch__loading {
  padding: 8px 0;
}

.sch__list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sch__item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-top: 1px solid var(--yg2-sep);
  flex-wrap: wrap;
}

.sch__main {
  min-width: 0;
}

.sch__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.sch__meta {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--yg2-text-secondary);
}

.sch__badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11.5px;
  background: var(--yg2-act-bg);
  color: var(--yg2-act-text);
}

.sch__badge.is-pending {
  background: #fdf3e3;
  color: #8a5a12;
}

.sch__badge.is-approved {
  background: #e6f5ed;
  color: #1f6b45;
}

.sch__badge.is-rejected,
.sch__badge.is-off {
  background: var(--yg2-act-danger-bg);
  color: var(--yg2-act-danger-text);
}

.sch__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sch__form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.sch__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--yg2-text-secondary);
}

.sch__field--wide {
  grid-column: 1 / -1;
}

.sch__textarea {
  min-height: 88px;
  resize: vertical;
  padding: 10px 12px;
  font: inherit;
}

.sch__submit {
  display: flex;
  justify-content: flex-end;
}

.sch__mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(6px);
}

.sch__modal {
  width: min(640px, 100%);
  max-height: 80dvh;
  overflow: auto;
  padding: 18px 20px;
  border-radius: var(--yg2-r-20);
  background: var(--yg2-surface);
  box-shadow: 0 30px 80px -20px color-mix(in srgb, #000 45%, transparent);
}

.sch__modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.sch__runs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sch__runs li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}

.sch__run-time {
  color: var(--yg2-text-secondary);
  white-space: nowrap;
}

.sch__run-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
