/**
 * 站内通知的运行时状态（横幅 / 收件页 / 资料页共用同一份视图）。
 *
 * 「已读」真源在服务端：`GET /v1/notifications/read` 给出 read 映射
 * （通知 oid -> 读到的单条版本号），`read[oid] >= notification.version` 即为已读；
 * 通知被管理员编辑（version + 1）后会重新变回未读。已读跟着账号走，换设备同样生效。
 *
 * 「不再显示」是设备级偏好，只落本地，永不上云。
 *
 * 读写的三段式：
 *   1) 乐观更新：先把 oid 记进 pendingRead，界面立刻不再算未读；
 *   2) POST /v1/notifications/{oid}/read 写穿服务端；
 *   3) 成功 → 用回执版本确认（confirmReadMark）；失败 → 回滚（rollbackReadMark），
 *      该通知会在下次检查时重新出现，界面据此给出轻量重试入口。
 *
 * 脏读防护：
 *   - 服务端 read 映射与本地 pending 一律「取大」合并，较慢的旧响应不会把刚写入的
 *     版本打回未读；
 *   - 账号切换用代际计数（generation）作废旧请求的响应，旧账号的通知不会漏进
 *     当前账号的视图。
 */
import { reactive } from 'vue'
import {
  listActive,
  listRead,
  markRead as markReadRequest,
  type NotificationRecord
} from '@/api/notifications'
import {
  accountNotificationState,
  applyServerReadMap,
  bindAccountNotificationState,
  claimReadWrite,
  clearSentReadMark,
  confirmReadMark,
  dropPendingReadMark,
  effectiveReadMap,
  notificationAccountKey,
  pendingServerWrites,
  queueReadMark,
  resetAccountNotificationState,
  rollbackReadMark,
  saveNotificationState,
  setReadWriteHandler,
  type NotificationMarker
} from '@/utils/notificationState'

/** 单次同步最多补写多少条历史已读（避免一次性打爆接口）。 */
const LEGACY_FLUSH_LIMIT = 30

export interface NotificationRuntimeState {
  /** 当前绑定的账号 id；空串表示未登录。 */
  accountKey: string
  /** 当前有效通知（服务端顺序，最新在前）。 */
  notifications: NotificationRecord[]
  /** 通知列表是否已经成功拉过一轮：决定「本地没有这条通知」是不是真的被删了。 */
  notificationsLoaded: boolean
  loading: boolean
  /** 拉取失败（列表或已读映射）。已有数据保留，避免闪烁。 */
  failed: boolean
  /** 最近一次「已读」写失败：界面据此给出轻量重试入口。 */
  readFailed: boolean
}

export const notificationRuntime = reactive<NotificationRuntimeState>({
  accountKey: '',
  notifications: [],
  notificationsLoaded: false,
  loading: false,
  failed: false,
  readFailed: false
})

/**
 * 代际计数：账号切换后旧请求的响应必须被丢弃，
 * 否则上一个账号的通知会漏进当前账号的视图。
 */
let generation = 0

/** 从服务端回执里取版本号（回执缺失时退回本地已知版本）。 */
function receiptVersion(receipt: { version?: number } | undefined, fallback: number): number {
  const version = Number(receipt?.version)
  return Number.isFinite(version) && version > 0 ? version : fallback
}

/**
 * 绑定账号。切换账号时立刻丢弃上一账号的通知列表与已读映射，
 * 只保留本地持久化的「不再显示」偏好。
 *
 * 绑定后立即拉一次已读映射：它体积小、且所有入口（横幅、收件页、资料页）
 * 都要用它判断未读，早一点拿到，页面上的未读标记才不会先闪一下「全未读」。
 */
export function bindNotificationAccount(accountId: string | number | null | undefined): void {
  const key = notificationAccountKey(accountId)
  if (notificationRuntime.accountKey === key) {
    return
  }
  const previous = notificationRuntime.accountKey
  generation += 1
  notificationRuntime.accountKey = key
  notificationRuntime.notifications = []
  notificationRuntime.notificationsLoaded = false
  notificationRuntime.loading = false
  notificationRuntime.failed = false
  notificationRuntime.readFailed = false
  if (previous) {
    resetAccountNotificationState(previous)
  }
  bindAccountNotificationState(key)
  if (key) {
    void syncReadMap()
  }
}

/** 生效中的已读映射：服务端真源 + 未确认的乐观写入。 */
export function readMap(): Record<string, number> {
  return effectiveReadMap(notificationRuntime.accountKey)
}

/** 某条通知当前是否未读（本地「不再显示」优先）。 */
export function isUnreadNotification(notice: NotificationMarker): boolean {
  if (!notice?.id) {
    return false
  }
  const version = Number(notice.version)
  if (!Number.isFinite(version) || version <= 0) {
    return false
  }
  if (accountNotificationState(notificationRuntime.accountKey).suppressed[notice.id]) {
    return false
  }
  const read = readMap()
  return version > (read[notice.id] ?? 0)
}

/** 未读通知（保持服务端顺序）。 */
export function unreadNotifications(): NotificationRecord[] {
  return notificationRuntime.notifications.filter((notice) => isUnreadNotification(notice))
}

/** 未读通知数量（角标 / 统计）。 */
export function unreadCount(): number {
  return unreadNotifications().length
}

/**
 * 拉取已读映射。体积小，登录后立即取：让所有入口立刻拿到服务端真源；
 * 通知列表仍由横幅按 10s 初始延迟 + 60s 节流单独拉取。
 */
export async function syncReadMap(signal?: AbortSignal): Promise<void> {
  const accountKey = notificationRuntime.accountKey
  if (!accountKey) {
    return
  }
  const requestGeneration = generation
  try {
    const serverRead = await listRead(signal)
    if (requestGeneration !== generation || notificationRuntime.accountKey !== accountKey) {
      return
    }
    applyServerReadMap(accountKey, serverRead)
  } catch {
    // 已读映射拉取失败：保留上一次的结果，不把通知误判成未读。
  }
}

/**
 * 同步：已读映射与通知列表并行拉取，合并后统一生效。
 * 任一失败只标记 failed，不清空已有数据（避免闪烁与误判未读）。
 */
export async function syncNotifications(signal?: AbortSignal): Promise<void> {
  const accountKey = notificationRuntime.accountKey
  if (!accountKey) {
    return
  }
  const requestGeneration = generation
  notificationRuntime.loading = true
  try {
    const [list, serverRead] = await Promise.all([listActive(signal), listRead(signal)])
    if (requestGeneration !== generation || notificationRuntime.accountKey !== accountKey) {
      // 账号已切换：丢弃这次响应。
      return
    }
    applyServerReadMap(accountKey, serverRead)
    notificationRuntime.notifications = list.notifications
    notificationRuntime.notificationsLoaded = true
    notificationRuntime.failed = false
    persistLocal(accountKey, list.version)
    void flushPendingWrites(requestGeneration, accountKey)
  } catch {
    if (requestGeneration !== generation) {
      return
    }
    notificationRuntime.failed = true
  } finally {
    if (requestGeneration === generation) {
      notificationRuntime.loading = false
    }
  }
}

/** 把「不再显示」与全局版本写回本地（已读不在本地持久化）。 */
function persistLocal(accountKey: string, globalVersion?: number): void {
  const account = accountNotificationState(accountKey)
  saveNotificationState(accountKey, {
    lastGlobalVersion: globalVersion ?? account.lastGlobalVersion,
    read: {},
    suppressed: account.suppressed
  })
}

/**
 * 把本地遗留 / 未确认的已读补写到服务端（幂等 upsert，失败即放弃）。
 *
 * 只有在通知列表已经完整拉过一轮时，才把「本地有、服务端列表里没有」的条目判定为
 * 已停用并丢弃；否则列表还没到就下结论，会把还没补写成功的记录误删。
 *
 * 认领走 claimReadWrite：已经发出过写穿请求的条目不会被这里重复 POST。
 */
async function flushPendingWrites(
  requestGeneration: number,
  accountKey: string
): Promise<void> {
  const pending = pendingServerWrites(accountKey)
  if (pending.length === 0) {
    return
  }
  const known = new Set(notificationRuntime.notifications.map((notice) => notice.id))
  let written = 0
  for (const { id, version } of pending) {
    if (requestGeneration !== generation || notificationRuntime.accountKey !== accountKey) {
      return
    }
    if (written >= LEGACY_FLUSH_LIMIT) {
      return
    }
    if (!known.has(id)) {
      if (notificationRuntime.notificationsLoaded) {
        // 通知已停用或删除：服务端无处安放这条记录，直接丢弃。
        dropPendingReadMark(accountKey, id)
      }
      continue
    }
    if (!claimReadWrite(accountKey, id, version)) {
      // 已发出过写穿请求，等回执即可。
      continue
    }
    written += 1
    try {
      const receipt = await markReadRequest(id)
      if (requestGeneration !== generation || notificationRuntime.accountKey !== accountKey) {
        return
      }
      confirmReadMark(accountKey, id, receiptVersion(receipt, version))
    } catch {
      // 补写失败不影响本次会话：本地仍然算已读，下次同步再试。
      clearSentReadMark(accountKey, id)
      return
    }
  }
}

/**
 * 标记已读：乐观更新 → POST → 成功对齐服务端版本 / 失败回滚。
 * 失败时该通知在下一次检查里会重新出现，界面据此给出重试入口。
 *
 * accountId 缺省用当前绑定的账号；资料页等自行持有账号 id 的入口可以显式传入。
 */
export async function markNotificationRead(
  notice: NotificationMarker,
  accountId?: string | number | null
): Promise<boolean> {
  const accountKey = notificationAccountKey(accountId ?? notificationRuntime.accountKey)
  if (!notice?.id || !accountKey) {
    return false
  }
  const version = Number(notice.version)
  if (!Number.isFinite(version) || version <= 0) {
    return false
  }
  const before = effectiveReadMap(accountKey)[notice.id] ?? 0
  queueReadMark(accountKey, notice.id, version)
  try {
    const receipt = await markReadRequest(notice.id)
    confirmReadMark(accountKey, notice.id, receiptVersion(receipt, version))
    notificationRuntime.readFailed = false
    return true
  } catch {
    // 回滚：恢复到写入前的版本，下次检查时这条通知重新出现。
    rollbackReadMark(accountKey, notice.id, before)
    notificationRuntime.readFailed = true
    return false
  }
}

/** 「不再显示」：仅本机当前账号永久隐藏，不上云。 */
export function suppressNotificationLocally(notice: NotificationMarker): void {
  const accountKey = notificationRuntime.accountKey
  if (!notice?.id || !accountKey) {
    return
  }
  const account = accountNotificationState(accountKey)
  account.suppressed[notice.id] = true
  persistLocal(accountKey)
}

/**
 * 写穿钩子：任何入口（资料页等）只要调用 saveNotificationState 标记已读，
 * 都会经由这里 POST 到服务端，已读因此处处跟着账号走。
 *
 * 挂在模块加载时注册：这个模块是全局单例，横幅一挂载它就在，
 * 资料页不必自己关心写穿细节。
 */
setReadWriteHandler((accountId, id, version) => {
  const key = notificationAccountKey(accountId)
  if (!key) {
    return
  }
  void markReadRequest(id)
    .then((receipt) => {
      confirmReadMark(key, id, receiptVersion(receipt, version))
    })
    .catch(() => {
      // 写穿失败：清掉「已发出」标记，允许下次重试；本地仍算已读，不重复打扰。
      clearSentReadMark(key, id)
    })
})
