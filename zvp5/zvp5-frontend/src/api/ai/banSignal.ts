/**
 * 封禁信号（403 + `USER_BANNED`）的统一落地点。
 *
 * 封禁有三条到达路径，各自在不同的层：
 * 1. axios 响应拦截器（普通业务请求，走 `reject` 分支）；
 * 2. AI zvp 面（`validateStatus: () => true`，非 2xx 走成功分支，由 `request()` 自己抛错）；
 * 3. AI 流式面（响应头已经是 200，封禁只能以流内 `error` 帧到达）。
 *
 * 三条路径命中封禁后要做的事完全一样：写 userStore 的 ban 状态（供 App.vue 的
 * 全屏遮罩用），再中止所有在途 AI 请求（否则界面还会继续吐字）。复制三份必然
 * 漂移，所以收口在这里。
 *
 * 契约：本函数**永不抛错**——封禁处理失败绝不能改变调用方原有的错误抛出与类型。
 */
import { useUserStore } from '@/stores/user'
import { readBanInfo } from '@/utils/ban'
import { abortAllAiRuns } from './activeRuns'

/** 后端表达「这个账号被封禁」的唯一错误码。 */
export const USER_BANNED_CODE = 'USER_BANNED'

/** 错误码是否为封禁信号（大小写/首尾空白容错：不同面给的码可能带空格）。 */
export function isUserBannedCode(code: string | null | undefined): boolean {
  return typeof code === 'string' && code.trim() === USER_BANNED_CODE
}

/**
 * 「这个响应是不是封禁」的统一判定：状态码 403 + 错误码 `USER_BANNED`。
 *
 * 只看 403 不够：业务里的权限不足也是 403（`AI_FORBIDDEN` 等），那些不该触发
 * 全站封禁扇出。
 */
export function isUserBannedResponse(
  status: number | undefined,
  code: string | null | undefined
): boolean {
  return status === 403 && isUserBannedCode(code)
}

export interface BanSignalOptions {
  /** 响应头（axios / fetch 的头对象都行，解析在 utils/ban.ts）。 */
  headers?: unknown
  /** 错误体里给出的原因；给了就优先于响应头。 */
  reason?: string
  /** 错误体里给出的到期毫秒时间戳（0 = 永久）；给了就优先于响应头。 */
  until?: number
}

/**
 * 落封禁状态并中止在途 AI 请求。幂等：重复命中不会重置倒计时、不会叠加中止。
 *
 * 未登录时只中止、不动 store：登出后到达的 in-flight 响应不该复活遮罩。
 */
export function handleUserBanned(options: BanSignalOptions = {}): void {
  try {
    const store = useUserStore()
    if (store.isLogin) {
      if (!store.bannedNoticeAt) {
        store.bannedNoticeAt = Date.now()
      }
      // 原因/到期每次命中都刷新（同一次登录可能先临时封禁、再改判永久）。
      const info = readBanInfo(options.headers)
      store.banReason = options.reason ?? info.reason
      store.banUntil = options.until ?? info.until
    }
  } catch {
    // store 没初始化 / 结构不符（测试替身）时忽略：封禁扇出不值得让原错误变形。
  }
  try {
    abortAllAiRuns('banned')
  } catch {
    // abortAllAiRuns 自身已保证不抛；这里再兜一层，防止替身/未来实现变更。
  }
}
