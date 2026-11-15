import { localeMessages } from '@/i18n/locales/namespaces'
import {
  AI_ERROR_FALLBACK_SECTION,
  aiErrorCopy,
  aiErrorOf,
  aiErrorSection
} from '@/api/ai/errors'

/**
 * 错误文案的统一收口。
 *
 * 后端 4xx/5xx 的 `detail` 大量是英文整句（Permission denied / User not found …），
 * 历史上前端每个页面各写一份「取 detail，取不到就兜底」的提取器，英文原样糊到用户脸上。
 * 这里把三件事收在一处：
 *   1. 从 axios 错误 / AiRequestError / 普通 Error 里取出可展示的 detail；
 *   2. 命中的英文整句换成中文（EN_ERROR_MESSAGES / INTERNAL_MESSAGE_PATTERNS）；
 *   3. 取不到就回落到调用方给的 i18n key，再回落到通用中文兜底。
 *
 * 未命中映射表的 detail 一律原样返回：宁可露出原文，也不把排查信息吞成一句「请求失败」。
 */

/** 彻底取不到任何可展示信息时的中文兜底。 */
export const DEFAULT_ERROR_MESSAGE = '请求失败，请稍后重试'

/**
 * 按 i18n key 取中文文案。
 *
 * 这里直接读 locales 的消息表（首屏字典 + 已懒加载的大字典），而不是走 vue-i18n
 * 的全局实例：本模块被 axios 拦截器在最早期引入，组件测试又经常把 vue-i18n 整体
 * mock 掉，依赖运行时实例会让「错误提示」这种基础设施变成测试的负担。项目只有
 * zh-CN 一套消息，按路径取值与 t() 等价；取不到就把 key 原样返回，便于立刻发现漏配。
 * 大字典（ai/manage/server/activity）由路由守卫装载，调用方所在页面必然已加载。
 */
export function translateKey(
  key: string,
  params?: Record<string, string | number>
): string {
  let node: unknown = localeMessages()
  for (const segment of key.split('.')) {
    if (!node || typeof node !== 'object') {
      return key
    }
    node = (node as Record<string, unknown>)[segment]
  }
  if (typeof node !== 'string') {
    return key
  }
  if (!params) {
    return node
  }
  // 只做 `{name}` 单层替换：本模块不跑 vue-i18n 的消息编译器，够 api 层拼 code 用。
  return node.replace(/\{(\w+)\}/g, (matched, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : matched
  )
}

/**
 * 后端高频英文 detail → 中文展示文案。
 *
 * 只收「整句原样」的 detail（errors.ts 的 ERROR_SECTIONS 是错误码，两者不重叠）；
 * 不做子串替换，避免把 detail 里的字段名/oid 一起改写。
 */
export const EN_ERROR_MESSAGES: Record<string, string> = {
  'Permission denied': '没有权限执行该操作',
  'User not found': '用户不存在',
  'User not in any class': '该用户不属于任何班级',
  'Attachment not found': '附件不存在',
  'Attachment unavailable': '附件暂不可用',
  'Attachment is verified': '附件已通过审核',
  'Attachment is deleted': '附件已被删除',
  'Activity not found': '活动不存在',
  'Activity member not found': '活动成员不存在',
  'Activity type and member mode do not match': '活动类型与成员模式不匹配',
  'Activity status is not changed': '活动状态没有变化',
  'Group not found': '用户组不存在',
  'Thumbnail not found': '缩略图不存在',
  'Task not found': '任务不存在',
  'Task not completed': '任务尚未完成',
  'Task not completed.': '任务尚未完成',
  'File not found': '文件不存在',
  'Invalid Object ID': '无效的对象 ID',
  'Invalid Object ID format': '对象 ID 格式无效',
  'Invalid activity ID format': '活动 ID 格式无效',
  'Invalid task ID': '任务 ID 格式无效',
  'Invalid token format': '令牌格式无效',
  'Invalid credential timestamp': '凭证时间戳无效',
  'Invalid or expired token': '令牌无效或已过期',
  'Token expired': '登录凭证已过期',
  'Invalid signature': '签名校验失败',
  'Invalid credential': '凭证无效',
  'Authentication required': '需要登录后才能操作',
  'Could not validate credentials': '无法验证身份凭证',
  'Password incorrect': '密码错误',
  'entity_type and entity_oid required': '缺少 entity_type 或 entity_oid 参数',
  'User already in activity with same counting mode': '该用户已在活动中，统计方式相同',
  'Unsupported search expression': '不支持的查询表达式',
  'Invalid search expression': '查询表达式无效',
  'Unsupported format': '不支持的文件格式',
  'Invalid query': '查询参数无效',
  'Query too long': '查询内容过长',
  'Invalid date filter': '日期筛选无效',
  'Invalid export date': '导出日期无效',
  'Invalid decision': '裁决结果无效',
  'Invalid status': '状态无效',
  'Invalid kind': '类型无效',
  'Invalid purpose': '用途无效',
  'Start date must precede end date': '开始日期必须早于结束日期',
  'Payload too large': '请求内容过大',
  'Missing file part': '缺少文件上传字段',
  'Permanently removed': '资源已被永久删除',
  'Excel file exceeds 10 MiB': 'Excel 文件超过 10 MiB 上限',
  'Invalid Excel file format': 'Excel 文件格式无效',
  'Workbook contains an unknown user': 'Excel 中存在未知用户',
  'Should be eight digits': '应为 8 位学号',
  'Should be Chinese name': '应为中文姓名',
  'Should be either name or number': '应为姓名或学号',
  'The past identity already exists.': '往届身份记录已存在',
  'Past identity not found.': '往届身份记录不存在',
  'Invalid past identity.': '往届身份无效',
  'Statistics are not available for this cohort.': '该届统计数据暂不可用',
  'Inconsistent date timezones': '日期时区不一致'
}

/**
 * 不该直接展示给用户的内部串 → 中文。命中即改写；未命中按原文处理。
 * 这些是 axios / 运行时的技术性 message，不是后端业务 detail。
 */
const INTERNAL_MESSAGE_PATTERNS: Array<[RegExp, string]> = [
  [/^timeout of \d+ms exceeded$/i, '请求超时，请稍后重试'],
  [/^network error$/i, '网络连接失败，请检查网络设置'],
  [/^canceled$/i, '请求已取消'],
  [/^failed to fetch$/i, '网络连接失败，请检查网络设置']
]

/** axios 自己拼的 message（"Request failed with status code 400"）没有展示价值。 */
const AXIOS_INTERNAL_PATTERN = /^request failed with status code \d+$/i

function translateMessage(text: string): string {
  const key = text.trim()
  if (!key) {
    return ''
  }
  const mapped = EN_ERROR_MESSAGES[key]
  if (mapped) {
    return mapped
  }
  for (const [pattern, replacement] of INTERNAL_MESSAGE_PATTERNS) {
    if (pattern.test(key)) {
      return replacement
    }
  }
  if (AXIOS_INTERNAL_PATTERN.test(key)) {
    return ''
  }
  return text
}

interface StructuredDetail {
  code?: unknown
  message?: unknown
}

/** Pydantic 风格的错误数组 → 一条可展示的整句（多条用「；」串起来）。 */
function joinArrayMessages(items: unknown[]): string {
  return items
    .map((item) => {
      if (typeof item === 'string') {
        return item
      }
      if (item && typeof item === 'object') {
        const structured = item as StructuredDetail
        if (typeof structured.message === 'string') {
          return structured.message
        }
      }
      return ''
    })
    .filter(Boolean)
    .join('；')
}

/** 从响应体里取出 detail / error.message 之类的可展示字段（不做翻译）。 */
function detailFromBody(body: unknown): string {
  if (typeof body === 'string') {
    return body.trim()
  }
  if (Array.isArray(body)) {
    return joinArrayMessages(body)
  }
  if (!body || typeof body !== 'object') {
    return ''
  }
  const record = body as Record<string, unknown>
  const detail = record.detail
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    return joinArrayMessages(detail)
  }
  if (detail && typeof detail === 'object') {
    const structured = detail as StructuredDetail
    if (typeof structured.message === 'string' && structured.message.trim()) {
      return structured.message
    }
    return JSON.stringify(detail)
  }
  const error = record.error
  if (typeof error === 'string') {
    return error
  }
  if (error && typeof error === 'object') {
    const structured = error as StructuredDetail
    if (typeof structured.message === 'string' && structured.message.trim()) {
      return structured.message
    }
  }
  return ''
}

/** 机器可读代号（AI_HTTP_500 / DEVICE_REQUIRED / USER_BANNED）不是可展示文案。 */
const MACHINE_CODE_PATTERN = /^[A-Z][A-Z0-9_]{2,}$/

function responseOf(error: unknown): { data?: unknown } | undefined {
  if (!error || typeof error !== 'object') {
    return undefined
  }
  const response = (error as { response?: unknown }).response
  return response && typeof response === 'object' ? (response as { data?: unknown }) : undefined
}

/**
 * 原始 detail（不翻译）。用于需要和机器可读协议串做比较的场景，
 * 例如 403 的 `detail === 'USER_BANNED'`。
 */
export function errorDetailOf(error: unknown): string {
  const response = responseOf(error)
  if (response) {
    const fromBody = detailFromBody(response.data)
    if (fromBody) {
      return fromBody
    }
  }
  if (!error || typeof error !== 'object') {
    return ''
  }
  const aiError = aiErrorOf(error)
  if (aiError) {
    return aiError.message?.trim() || aiError.code
  }
  const record = error as Record<string, unknown>
  if (typeof record.detail === 'string' && record.detail.trim()) {
    return record.detail
  }
  const message = typeof record.message === 'string' ? record.message.trim() : ''
  if (message) {
    return message
  }
  // aiRequest 会把字符串 detail 塞进 code（message 为空），这里一并取出。
  return typeof record.code === 'string' ? record.code.trim() : ''
}

/** 把已经是「detail 值」的对象/字符串翻译成中文展示串；空值返回 ''。 */
export function resolveDetailMessage(detail: unknown): string {
  const text = detailFromBody(detail)
  return text ? translateMessage(text) : ''
}

/** AI 网关错误：沿用 api/ai/errors 的「标题：建议」，映射表里没有的码原样展示。 */
function resolveAiErrorMessage(error: unknown): string | null {
  const aiError = aiErrorOf(error)
  if (!aiError) {
    return null
  }
  // AiRequestError 构造时会把 code 兜成 message；这种「没有正文」的情况要走映射表。
  if (aiError.message && aiError.message !== aiError.code) {
    return translateMessage(aiError.message)
  }
  if (aiErrorSection(aiError.code) === AI_ERROR_FALLBACK_SECTION) {
    return aiError.code
  }
  const copy = aiErrorCopy(aiError.code, translateKey)
  return `${copy.title}：${copy.hint}`
}

/**
 * 取不到兜底文案时统一的空串语义：调用方可以 `|| 自己的默认文案`。
 */
export function resolveErrorMessageOr(error: unknown, fallbackText: string): string {
  const message = resolveDetailChain(error)
  return message || fallbackText
}

function resolveDetailChain(error: unknown): string {
  const aiMessage = resolveAiErrorMessage(error)
  if (aiMessage) {
    return aiMessage
  }
  const response = responseOf(error)
  if (response) {
    // 有响应体时只认后端 detail；axios 的 message（Request failed with status code 400）
    // 只是状态码复述，没有任何展示价值，取不到 detail 就交给调用方兜底。
    return translateMessage(detailFromBody(response.data))
  }
  const detail = errorDetailOf(error)
  if (!detail) {
    return ''
  }
  // 没有响应体的异常（Network Error / timeout / 普通 Error 或 aiRequest 的结构化错误）：
  // 机器代号（AI_HTTP_500 / DEVICE_REQUIRED）不是人话，一律交给调用方兜底；
  // 其余命中映射表就换中文，未命中的原文保留，避免吞掉排查信息。
  if (MACHINE_CODE_PATTERN.test(detail)) {
    return ''
  }
  return translateMessage(detail)
}

/**
 * 任意异常 → 可直接展示的中文错误文案。
 *
 * 优先级：AI 错误码 → 后端 detail 命中映射表 → 后端 detail 原文 →
 * fallbackKey（i18n key）→ 「请求失败，请稍后重试」。
 */
export function resolveErrorMessage(error: unknown, fallbackKey?: string): string {
  const message = resolveDetailChain(error)
  if (message) {
    return message
  }
  if (fallbackKey) {
    return translateKey(fallbackKey)
  }
  return DEFAULT_ERROR_MESSAGE
}
