import type {
  AiScheduledReviewDecision,
  AiScheduledReviewStatus,
  AiScheduledRunNowResult,
  AiScheduledTask,
  AiScheduledTaskCreateInput,
  AiScheduledTaskRun,
  AiScheduledTaskUpdateInput
} from '@/../types/ai'
import { aiRequest, aiRequestList, aiRequestVoid } from './http'

/**
 * AI 定时任务（/ai/scheduled-tasks）。
 *
 * 四条与其它 AI 端点不同的地方，前端必须记住：
 *
 * 1. **本人的列表不分页**。不带管理参数时，后端一次把该 owner 的全部任务回给
 *    前端；``limit`` / ``offset`` 只对管理端视图生效。
 * 2. **管理端视图有两个开关**。``all_tasks=1`` 跨全部用户列任务；
 *    ``review_status`` 按审核状态过滤。两者都只有 admin 能用，非 admin 传了
 *    直接 403 AI_SCHEDULE_FORBIDDEN。
 * 3. **metadata.size 是当前页条数，不是总数**。「加载更多」就是拿它跟 limit 比。
 * 4. **创建即待审**。review_status 由服务端决定（新建是 pending），前端不传；
 *    改提示词会打回 draft —— 上一轮的「已通过」是对旧文案的判断，前端必须在
 *    编辑框里明确提示用户这一点。
 */

/** 最小间隔（分钟）：与后端 scheduled_runner.MIN_INTERVAL_MINUTES 对齐。 */
export const AI_SCHEDULE_MIN_INTERVAL_MINUTES = 15
/** 默认间隔（分钟）：与后端 scheduled_runner.DEFAULT_INTERVAL_MINUTES 对齐（24 小时）。 */
export const AI_SCHEDULE_DEFAULT_INTERVAL_MINUTES = 1440
/** 提示词长度上限：与后端 TaskCreate.prompt_text 的 max_length 对齐。 */
export const AI_SCHEDULE_PROMPT_MAX = 20000
/** 名称长度上限：与后端 TaskCreate.name 的 max_length 对齐。 */
export const AI_SCHEDULE_NAME_MAX = 128
/** cron 表达式长度上限：与后端 TaskCreate.cron_expr 的 max_length 对齐。 */
export const AI_SCHEDULE_CRON_MAX = 64
/**
 * 学生会（department）的任务条数上限：与后端 services/ai/config.py 的
 * AI_SCHEDULE_QUOTA_DEPARTMENT=1 对齐。
 *
 * 这个值可在服务端运行时覆盖，所以本地只作提示；真正的裁决在创建时的
 * 409 AI_SCHEDULE_QUOTA_EXCEEDED。
 */
export const AI_SCHEDULE_DEPARTMENT_QUOTA = 1

/** crontab 五段的取值范围（分、时、日、月、周）。 */
const CRON_FIELD_RANGES = [
  { min: 0, max: 59 },
  { min: 0, max: 23 },
  { min: 1, max: 31 },
  { min: 1, max: 12 },
  { min: 0, max: 7 }
] as const

/** 星期缩写：后端用 APScheduler 的 from_crontab，这几个名字它认。 */
const CRON_WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

/**
 * 单段是否合法。
 *
 * 支持 `*`、`5`、`1-10`、`1-10/2` 与逗号并列，也支持「星号斜杠 N」的整段步长；
 * 星期段还认 mon-sun 这类名字。名字与数字混排（mon-5）交给后端裁决，本地不拦。
 */
function isValidCronField(field: string, min: number, max: number, allowNames: boolean): boolean {
  const text = field.trim().toLowerCase()
  if (!text) {
    return false
  }
  for (const segment of text.split(',')) {
    const pieces = segment.split('/')
    if (pieces.length > 2) {
      return false
    }
    const [rangePart, stepPart] = pieces
    if (stepPart !== undefined) {
      const step = Number(stepPart)
      if (!Number.isInteger(step) || step < 1) {
        return false
      }
    }
    if (rangePart === '*') {
      continue
    }
    const bounds = rangePart.split('-')
    if (bounds.length > 2) {
      return false
    }
    const numeric: number[] = []
    for (const bound of bounds) {
      if (allowNames && CRON_WEEKDAYS.includes(bound)) {
        continue
      }
      if (!/^\d+$/.test(bound)) {
        return false
      }
      const value = Number(bound)
      if (value < min || value > max) {
        return false
      }
      numeric.push(value)
    }
    if (numeric.length === 2 && numeric[1]! < numeric[0]!) {
      return false
    }
  }
  return true
}

/**
 * 5 段式 crontab 是否合法（本地预检）。
 *
 * 后端用 APScheduler 解析，真正的裁决在服务端（422 AI_SCHEDULE_BAD_CRON）；
 * 本地校验只为在失焦时立刻给一句人话，省掉一次往返。
 */
export function isValidCronExpression(expr: string): boolean {
  const text = String(expr ?? '').trim()
  if (!text || text.length > AI_SCHEDULE_CRON_MAX) {
    return false
  }
  const fields = text.split(/\s+/)
  if (fields.length !== 5) {
    return false
  }
  return fields.every((field, index) => {
    const range = CRON_FIELD_RANGES[index]!
    return isValidCronField(field, range.min, range.max, index === 4)
  })
}

/** 列任务。管理端开关（allTasks / reviewStatus）只有 admin 能用。 */
export interface AiScheduledTasksQuery {
  /** admin only：跨全部用户列任务。对应 query 的 all_tasks=1。 */
  allTasks?: boolean
  /** admin only：按审核状态过滤（draft/pending/approved/rejected）。 */
  reviewStatus?: AiScheduledReviewStatus
  /** 限定某个 owner；admin 可用它查他人的任务。 */
  ownerOid?: string
  /** 每页条数（1..500）。只对管理端视图生效。 */
  limit?: number
  /** 偏移量；「加载更多」靠它往后翻。 */
  offset?: number
}

/** 列表的一页。size 是**当前页条数**（后端 metadata.size），不是总数。 */
export interface AiScheduledTasksPage {
  rows: AiScheduledTask[]
  size: number
}

export async function listAiScheduledTasks(
  query: AiScheduledTasksQuery = {}
): Promise<AiScheduledTasksPage> {
  const params: Record<string, string | number> = {}
  if (query.allTasks) {
    params.all_tasks = 1
  }
  const status = query.reviewStatus?.trim()
  if (status) {
    params.review_status = status
  }
  const owner = query.ownerOid?.trim()
  if (owner) {
    params.owner_oid = owner
  }
  const limit = Number(query.limit)
  if (Number.isFinite(limit) && limit >= 1) {
    params.limit = Math.trunc(limit)
  }
  const offset = Number(query.offset)
  if (Number.isFinite(offset) && offset > 0) {
    params.offset = Math.trunc(offset)
  }
  const result = await aiRequestList<AiScheduledTask[]>({
    method: 'get',
    url: '/ai/scheduled-tasks',
    params
  })
  const rows = Array.isArray(result.data) ? result.data : []
  return {
    rows,
    // 后端的 metadata.size 就是本页条数；缺 metadata 时用实际行数兜底。
    size: result.metadata.size > 0 ? result.metadata.size : rows.length
  }
}

/** 建任务（review_status 由服务端置为 pending）。 */
export async function createAiScheduledTask(
  input: AiScheduledTaskCreateInput
): Promise<AiScheduledTask> {
  return aiRequest<AiScheduledTask>({
    method: 'post',
    url: '/ai/scheduled-tasks',
    data: input
  })
}

/** 部分更新；改 prompt_text 会让服务端把审核结论打回 draft。 */
export async function updateAiScheduledTask(
  oid: string,
  input: AiScheduledTaskUpdateInput
): Promise<AiScheduledTask> {
  return aiRequest<AiScheduledTask>({
    method: 'patch',
    url: `/ai/scheduled-tasks/${encodeURIComponent(oid)}`,
    data: input
  })
}

/** 删除成功返回 204，没有响应体。 */
export async function deleteAiScheduledTask(oid: string): Promise<void> {
  await aiRequestVoid({
    method: 'delete',
    url: `/ai/scheduled-tasks/${encodeURIComponent(oid)}`
  })
}

/** 审核（仅 admin）：通过或驳回，驳回时可附备注。 */
export async function reviewAiScheduledTask(
  oid: string,
  decision: AiScheduledReviewDecision,
  note?: string
): Promise<AiScheduledTask> {
  const payload: { decision: AiScheduledReviewDecision; note?: string } = { decision }
  const text = note?.trim()
  if (text) {
    payload.note = text
  }
  return aiRequest<AiScheduledTask>({
    method: 'post',
    url: `/ai/scheduled-tasks/${encodeURIComponent(oid)}/review`,
    data: payload
  })
}

/** 立即执行一次：只有 review_status='approved' 的任务才允许。 */
export async function runAiScheduledTaskNow(oid: string): Promise<AiScheduledRunNowResult> {
  return aiRequest<AiScheduledRunNowResult>({
    method: 'post',
    url: `/ai/scheduled-tasks/${encodeURIComponent(oid)}/run-now`
  })
}

/** 执行历史（分页，新的在前）。 */
export async function listAiScheduledTaskRuns(
  oid: string,
  page = 1,
  size = 20
): Promise<{ rows: AiScheduledTaskRun[]; total: number }> {
  const safePage = Math.max(1, Math.trunc(page) || 1)
  const safeSize = Math.min(100, Math.max(1, Math.trunc(size) || 20))
  const result = await aiRequestList<AiScheduledTaskRun[]>({
    method: 'get',
    url: `/ai/scheduled-tasks/${encodeURIComponent(oid)}/runs`,
    params: { page: safePage, size: safeSize }
  })
  return {
    rows: Array.isArray(result.data) ? result.data : [],
    total: result.metadata.size
  }
}
