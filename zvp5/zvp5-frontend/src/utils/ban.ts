/**
 * 封禁响应头解析(公共工具)。
 *
 * 为什么单独抽出来:后端 `POST /api/v1/users/auth` 被拒时用 403 + `detail: USER_BANNED`
 * 表达封禁,具体原因/期限放在 `X-Ban-Reason`(URL 编码,可能含中文)与
 * `X-Ban-Until`(到期 epoch 秒,空串=永久)里。axios 拦截器与登录页都要读这两条头,
 * 各写一份容易漂移,所以统一收口在这里。
 *
 * 本工具刻意不依赖 i18n / dayjs:调用方决定格式化文案,便于各页面复用同一份解析。
 */
export interface BanInfo {
  /** 已解码的封禁原因;缺失或解码失败时为原始串,再缺省为空串。 */
  reason: string
  /** 到期时间戳(毫秒);0 表示永久封禁。 */
  until: number
}

/**
 * 读取单个响应头。axios 的 headers 可能是 AxiosHeaders(有 get 方法)也可能是普通对象
 * (测试里手写的 adapter / 旧版适配器),数组值取第一项,取不到返回 undefined。
 */
function readHeader(headers: unknown, name: string): string | undefined {
  const bag = headers as
    | ({ get?: (key: string) => unknown } & Record<string, unknown>)
    | undefined
  const raw = bag?.get?.(name) ?? bag?.[name]
  if (typeof raw === 'string') {
    return raw
  }
  if (Array.isArray(raw) && raw.length > 0) {
    return String(raw[0])
  }
  return undefined
}

export function readBanInfo(headers: unknown): BanInfo {
  const rawReason = readHeader(headers, 'x-ban-reason')
  let reason = ''
  if (rawReason !== undefined) {
    // 原因可能含中文且经过 URL 编码;坏编码不能让封禁信号本身失败,兜底用原始串。
    try {
      reason = decodeURIComponent(rawReason)
    } catch {
      reason = rawReason
    }
  }

  const rawUntil = readHeader(headers, 'x-ban-until')
  let until = 0
  if (rawUntil !== undefined) {
    // 契约:空串 = 永久封禁;其余是到期 epoch 秒。非正数/无法解析都按永久(0)处理。
    const seconds = rawUntil.trim() === '' ? 0 : Number(rawUntil)
    until = Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 0
  }

  return { reason, until }
}

/**
 * 期限行的展示文案。永久(0)由调用方给文案,这里返回空串表示「无需展示到期时间」,
 * 避免 util 依赖具体语言。
 */
export function formatBanUntil(until: number, format: (ms: number) => string): string {
  return until > 0 ? format(until) : ''
}
