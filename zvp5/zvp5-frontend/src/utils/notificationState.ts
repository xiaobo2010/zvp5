/**
 * 站内通知的状态模型。
 *
 * 「已读」的真源在服务端：`GET /v1/notifications/read` 返回 read 映射
 * （通知 oid -> 用户读到的单条版本号），`read[oid] >= notification.version` 即为已读；
 * 通知被管理员编辑（version + 1）后会重新变回未读。已读状态跟着账号走，
 * 换设备登录同样生效。
 *
 * 本地（localStorage）只保留两样东西：
 *   - suppressed：「不再显示」，设备级偏好，永不上云；
 *   - lastGlobalVersion：服务端全局版本，仅用于诊断与刷新节流。
 *
 * 「已读」在内存里按账号分四层（见 AccountNotificationState）：
 *   - serverRead：服务端确认过的已读版本（权威）；
 *   - pendingRead：乐观写入、尚未被服务端确认的版本；
 *   - sentRead    ：已经发出过写穿请求的版本（避免重复 POST）；
 *   - read        ：serverRead 与 pendingRead 的合并结果，响应式共享，
 *                   横幅 / 收件页 / 资料页看到的是同一份视图。
 *
 * 合并一律「取大」：较慢的旧响应不能把刚写入的版本打回去（脏读防护）。
 */
import { reactive, toRaw } from 'vue'

export interface NotificationMarker {
  id: string
  version: number
}

/** 已读版本映射：通知 oid -> 读到的单条版本号。 */
export type NotificationReadMap = Record<string, number>

export interface NotificationState {
  /** 上次从服务端拿到/处理的全局版本，仅用于诊断和后续刷新优化。 */
  lastGlobalVersion: number
  /** 生效中的已读映射：服务端真源 + 尚未确认的乐观写入。 */
  read: NotificationReadMap
  /** 不再显示：按通知 id 永久压制，编辑导致版本增大也不会重新弹出。 */
  suppressed: Record<string, true>
}

/**
 * 账号级内存态。`read` 是响应式对象且就地更新（不整体替换），
 * 这样任何持有它的视图（横幅、收件页、资料页）都会跟着服务端同步结果一起刷新。
 */
export interface AccountNotificationState {
  /** 生效中的已读映射：服务端真源 + 未确认的乐观写入。 */
  read: NotificationReadMap
  /** 服务端确认过的已读版本。 */
  serverRead: NotificationReadMap
  /** 尚未被服务端确认的写入。 */
  pendingRead: NotificationReadMap
  /** 已经发出过写穿请求的版本（避免重复 POST）。 */
  sentRead: NotificationReadMap
  /** 「不再显示」：设备级永久压制。 */
  suppressed: Record<string, true>
  /** 服务端全局版本，仅用于诊断与刷新节流。 */
  lastGlobalVersion: number
  /** 是否已经载入过本地持久化部分。 */
  bound: boolean
}

/** 本地存储 key 必须绑定账号唯一 id（优先使用用户 oid）。 */
export const NOTIFICATION_STATE_PREFIX = 'notification-state:'

const UNSAFE_KEYS = new Set(['__proto__', 'constructor', 'prototype'])

/**
 * Minimal storage contract so tests (and environments without localStorage) can
 * inject an in-memory / throwing implementation.
 */
export interface NotificationStateStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

/**
 * 已读写穿钩子：任何入口把「已读」写进本地状态时，都会顺带通知网络层
 * 把这条记录 POST 到服务端，这样已读才真正跟着账号走。
 */
export type ReadWriteHandler = (
  accountId: string | number | null | undefined,
  id: string,
  version: number
) => void

let readWriteHandler: ReadWriteHandler | null = null

/** 注册已读写穿处理器（网络层调用）。返回取消注册的函数。 */
export function setReadWriteHandler(handler: ReadWriteHandler | null): () => void {
  readWriteHandler = handler
  return () => {
    if (readWriteHandler === handler) {
      readWriteHandler = null
    }
  }
}

export function emptyNotificationState(): NotificationState {
  return {
    lastGlobalVersion: 0,
    read: {},
    suppressed: {}
  }
}

export function emptyAccountNotificationState(): AccountNotificationState {
  return {
    read: {},
    serverRead: {},
    pendingRead: {},
    sentRead: {},
    suppressed: {},
    lastGlobalVersion: 0,
    bound: false
  }
}

/**
 * 账号 key 归一化。
 *
 * 资料页用 `user._id || user.id`、横幅用 `userStore._id || userStore.id`，
 * 两处都可能在资料尚未加载完时拿到数字 id（0）。这里统一成同一个字符串，
 * 免得同一账号在两处落进不同的内存态。
 */
export function notificationAccountKey(
  accountId: string | number | null | undefined
): string {
  const key = String(accountId ?? '').trim()
  return key === '0' ? '' : key
}

export function notificationStateKey(accountId: string | number | null | undefined): string {
  const id = notificationAccountKey(accountId)
  return id ? `${NOTIFICATION_STATE_PREFIX}${id}` : ''
}

function normalizeVersion(value: unknown): number {
  const version = Number(value)
  return Number.isFinite(version) && version > 0 ? Math.floor(version) : 0
}

function normalizeGlobalVersion(value: unknown): number {
  const version = Number(value)
  return Number.isFinite(version) && version > 0 ? Math.floor(version) : 0
}

function safeEntries(value: unknown): Array<[string, unknown]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return []
  }
  return Object.entries(value as Record<string, unknown>).filter(
    ([id]) => Boolean(id) && !UNSAFE_KEYS.has(id)
  )
}

/**
 * Parse a persisted state defensively. Invalid JSON, proto-pollution keys and
 * non-numeric versions all degrade to an empty/partial state rather than
 * breaking app startup.
 *
 * 旧版本持久化里的 read 字段仍会被解析出来：那是「已读上云」之前的历史已读标记，
 * 迁移期用来避免已读过的旧通知重新弹一遍（见 bindAccountNotificationState）。
 */
export function parseNotificationState(raw: string | null | undefined): NotificationState {
  const state = emptyNotificationState()
  if (!raw) {
    return state
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return state
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return state
  }

  const record = parsed as Record<string, unknown>
  state.lastGlobalVersion = normalizeGlobalVersion(record.lastGlobalVersion)

  for (const [id, version] of safeEntries(record.read)) {
    const normalized = normalizeVersion(version)
    if (normalized > 0) {
      state.read[id] = normalized
    }
  }

  for (const [id, value] of safeEntries(record.suppressed)) {
    if (value === true) {
      state.suppressed[id] = true
    }
  }

  return state
}

/**
 * 序列化：只落设备级偏好与服务端全局版本。
 *
 * 「已读」的真源在服务端，本地不留已读记录；迁移期读取旧数据时用到的
 * loadLegacyReadMap 读的是旧版本写下的 read 字段，用于一次性补写。
 */
export function serializeNotificationState(state: NotificationState): string {
  const suppressed: Record<string, true> = {}
  for (const [id, value] of safeEntries(state.suppressed ?? {})) {
    if (value === true) {
      suppressed[id] = true
    }
  }

  return JSON.stringify({
    lastGlobalVersion: normalizeGlobalVersion(state.lastGlobalVersion),
    suppressed
  })
}

function defaultStorage(): NotificationStateStorage | null {
  try {
    return typeof globalThis.localStorage === 'undefined' ? null : globalThis.localStorage
  } catch {
    // Privacy mode / blocked storage throws on access; notifications must not
    // break the rest of the app.
    return null
  }
}

/* -------------------------------------------------------------------------
   账号级内存态
   ------------------------------------------------------------------------- */

const accountStates = reactive<Record<string, AccountNotificationState>>({})

/**
 * 账号已读映射对象 → 账号 key。
 *
 * markNotificationRead 是纯函数，只拿到一份状态对象；通过这张反查表可以判断
 * 它的 read 是不是某个账号的共享映射，是的话顺带把这次写入记进该账号的
 * pendingRead ——否则下一次同步重算映射时，这条刚标记的通知会被冲掉。
 *
 * 键用的是「原始对象」：Vue 的 reactive() 返回的是代理，代理与原对象不是同一个
 * 引用，查询时统一 toRaw() 才能对上。
 */
const accountKeyByReadMap = new WeakMap<object, string>()

/** 未绑定账号（未登录）时也有一份稳定对象，避免各处拿到不同的空映射。 */
const anonymousAccount = emptyAccountNotificationState()
accountKeyByReadMap.set(toRaw(anonymousAccount.read), '')

/** 取（并按需初始化）某个账号的内存态。 */
export function accountNotificationState(
  accountId: string | number | null | undefined
): AccountNotificationState {
  const key = notificationAccountKey(accountId)
  if (!key) {
    return anonymousAccount
  }
  let state = accountStates[key]
  if (!state) {
    state = emptyAccountNotificationState()
    accountStates[key] = state
    accountKeyByReadMap.set(toRaw(state.read), key)
  }
  return state
}

/**
 * 绑定账号：载入本地持久化部分，并把上云前的历史已读标记作为待补写项。
 * 每个账号只载入一次：重复载入会冲掉「不再显示」与待补写的已读。
 */
export function bindAccountNotificationState(
  accountId: string | number | null | undefined,
  storage: NotificationStateStorage | null = defaultStorage()
): AccountNotificationState {
  const state = accountNotificationState(accountId)
  if (state.bound) {
    return state
  }
  const key = notificationAccountKey(accountId)
  state.bound = true
  if (!key) {
    return state
  }
  const local = parseNotificationState(readRaw(notificationStateKey(key), storage))
  // 就地写入：suppressed 是共享响应式对象，换引用会让别处拿到旧快照。
  for (const [id, value] of safeEntries(local.suppressed)) {
    if (value === true) {
      state.suppressed[id] = true
    }
  }
  state.lastGlobalVersion = local.lastGlobalVersion
  // 上云前的历史已读：当作待补写项，本机不再重复打扰，稍后由网络层补写到服务端。
  state.pendingRead = { ...local.read }
  refreshReadMap(state)
  return state
}

/** 丢弃某个账号的内存态（登出 / 切换账号）。 */
export function resetAccountNotificationState(
  accountId: string | number | null | undefined
): void {
  const key = notificationAccountKey(accountId)
  if (key) {
    delete accountStates[key]
  }
}

/**
 * 就地重算生效中的已读映射（serverRead + pendingRead）。
 * 就地更新而不是整体替换：持有该对象的视图会跟着一起刷新。
 */
function refreshReadMap(state: AccountNotificationState): void {
  const merged = mergeReadMap(state.serverRead, state.pendingRead)
  for (const id of Object.keys(state.read)) {
    if (!(id in merged)) {
      delete state.read[id]
    }
  }
  for (const [id, version] of Object.entries(merged)) {
    state.read[id] = version
  }
}

/**
 * 应用服务端 read 映射：服务端版本更大时生效，
 * 已被服务端确认的待写项随之清除（脏读防护：旧响应不能把新写入打回去）。
 */
export function applyServerReadMap(
  accountId: string | number | null | undefined,
  serverRead: NotificationReadMap | undefined | null
): AccountNotificationState {
  const state = accountNotificationState(accountId)
  state.serverRead = mergeReadMap(state.serverRead, serverRead)
  for (const [id, version] of Object.entries(state.pendingRead)) {
    if ((state.serverRead[id] ?? 0) >= version) {
      delete state.pendingRead[id]
    }
  }
  refreshReadMap(state)
  return state
}

/**
 * 生效中的已读映射：服务端真源 + 未确认的乐观写入。
 * 返回的是共享的响应式对象，视图可直接依赖它。
 */
export function effectiveReadMap(
  accountId: string | number | null | undefined
): NotificationReadMap {
  return accountNotificationState(accountId).read
}

/** 记录一次乐观写入（尚未被服务端确认）。 */
export function queueReadMark(
  accountId: string | number | null | undefined,
  id: string,
  version: number
): void {
  const normalized = normalizeVersion(version)
  if (!id || normalized <= 0) {
    return
  }
  const state = accountNotificationState(accountId)
  state.pendingRead[id] = Math.max(state.pendingRead[id] ?? 0, normalized)
  refreshReadMap(state)
}

/** 服务端确认写入：提升到真源层并清掉待写项。 */
export function confirmReadMark(
  accountId: string | number | null | undefined,
  id: string,
  version: number
): void {
  const normalized = normalizeVersion(version)
  if (!id || normalized <= 0) {
    return
  }
  const state = accountNotificationState(accountId)
  state.serverRead[id] = Math.max(state.serverRead[id] ?? 0, normalized)
  if ((state.pendingRead[id] ?? 0) <= normalized) {
    delete state.pendingRead[id]
  }
  delete state.sentRead[id]
  refreshReadMap(state)
}

/** 回滚一次失败的写入：恢复写入前的版本（0 表示此前未读，直接删除该条目）。 */
export function rollbackReadMark(
  accountId: string | number | null | undefined,
  id: string,
  previousVersion: number
): void {
  if (!id) {
    return
  }
  const state = accountNotificationState(accountId)
  const previous = normalizeVersion(previousVersion)
  if (previous > 0) {
    state.pendingRead[id] = previous
  } else {
    delete state.pendingRead[id]
  }
  delete state.sentRead[id]
  refreshReadMap(state)
}

/** 丢弃一条待补写的已读标记（对应通知已停用或删除）。 */
export function dropPendingReadMark(
  accountId: string | number | null | undefined,
  id: string
): void {
  const state = accountNotificationState(accountId)
  if (id in state.pendingRead) {
    delete state.pendingRead[id]
    refreshReadMap(state)
  }
}

/**
 * 待补写到服务端的已读项：本地比服务端多出来的那部分
 * （上云前的历史已读 + 尚未收到确认的乐观写入）。
 */
export function pendingServerWrites(
  accountId: string | number | null | undefined
): Array<{ id: string; version: number }> {
  const state = accountNotificationState(accountId)
  const pending: Array<{ id: string; version: number }> = []
  for (const [id, version] of Object.entries(state.pendingRead)) {
    if ((state.serverRead[id] ?? 0) < version) {
      pending.push({ id, version })
    }
  }
  return pending
}

/* -------------------------------------------------------------------------
   本地持久化（设备级偏好）
   ------------------------------------------------------------------------- */

/** 读取本地存储原始串（异常时降级为空）。 */
function readRaw(key: string, storage: NotificationStateStorage | null): string | null {
  if (!storage || !key) {
    return null
  }
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

/**
 * 读取账号当前的已读视图。
 *
 * 返回的 read / suppressed 是账号共享的响应式对象（不是快照）：
 * 横幅、收件页、资料页拿到的都是同一份，任一处标记已读，其它地方立刻跟着变。
 */
export function loadNotificationState(
  accountId: string | number | null | undefined,
  storage: NotificationStateStorage | null = defaultStorage()
): NotificationState {
  bindAccountNotificationState(accountId, storage)
  const account = accountNotificationState(accountId)
  return {
    lastGlobalVersion: account.lastGlobalVersion,
    suppressed: account.suppressed,
    read: account.read
  }
}

/**
 * 读取「已读上云」之前遗留的本地已读标记。
 * 仅用于迁移：这些通知在服务端还没有已读记录，迁移期避免它们重新弹一遍。
 */
export function loadLegacyReadMap(
  accountId: string | number | null | undefined,
  storage: NotificationStateStorage | null = defaultStorage()
): NotificationReadMap {
  const key = notificationStateKey(accountId)
  if (!key || !storage) {
    return {}
  }
  try {
    return parseNotificationState(storage.getItem(key)).read
  } catch {
    return {}
  }
}

/**
 * 认领一次「写穿」机会：这条已读还没被服务端确认、也还没发出去过。
 *
 * 返回 true 的调用方负责发出写穿请求；发完由 confirmReadMark / clearSentReadMark
 * 收尾。这样「本地标记已读 → 写服务端」只有一条路径，不会重复 POST。
 */
export function claimReadWrite(
  accountId: string | number | null | undefined,
  id: string,
  version: number
): boolean {
  const normalized = normalizeVersion(version)
  if (!id || normalized <= 0) {
    return false
  }
  const account = accountNotificationState(accountId)
  // 服务端已确认：不必再写。
  if ((account.serverRead[id] ?? 0) >= normalized) {
    return false
  }
  // 已经发出去过：等回执，不重复 POST。
  if ((account.sentRead[id] ?? 0) >= normalized) {
    return false
  }
  account.sentRead[id] = normalized
  queueReadMark(accountId, id, normalized)
  return true
}

/**
 * 写回本地状态：只落「不再显示」与服务端全局版本。
 *
 * state.read 里服务端还不知道、且本次还没发过的条目会被记成待补写项，
 * 并交给已注册的写穿处理器（网络层）写到服务端——这样任何入口
 * （横幅、收件页、资料页）标记已读都会上云。
 */
export function saveNotificationState(
  accountId: string | number | null | undefined,
  state: NotificationState,
  storage: NotificationStateStorage | null = defaultStorage()
): boolean {
  const key = notificationStateKey(accountId)
  const account = accountNotificationState(accountId)
  if (key) {
    // 就地合并：账号的 suppressed 是共享响应式对象，换引用会让别处拿到旧快照。
    for (const [id, value] of safeEntries(state.suppressed ?? {})) {
      if (value === true) {
        account.suppressed[id] = true
      }
    }
    account.lastGlobalVersion = normalizeGlobalVersion(state.lastGlobalVersion)
    for (const [id, version] of safeEntries(state.read ?? {})) {
      const normalized = normalizeVersion(version)
      if (!claimReadWrite(accountId, id, normalized)) {
        continue
      }
      readWriteHandler?.(accountId, id, normalized)
    }
  }
  if (!key || !storage) {
    return false
  }
  try {
    storage.setItem(key, serializeNotificationState(state))
    return true
  } catch {
    return false
  }
}

/**
 * 写穿失败：清掉「已发出」的标记，允许下次重试。
 */
export function clearSentReadMark(
  accountId: string | number | null | undefined,
  id: string
): void {
  const state = accountNotificationState(accountId)
  if (id in state.sentRead) {
    delete state.sentRead[id]
  }
}

/* -------------------------------------------------------------------------
   判定与操作（纯函数）
   ------------------------------------------------------------------------- */

/**
 * 合并多份已读映射：同一个 oid 取版本号较大者。
 *
 * 取最大值而不是「后者覆盖前者」，是为了防脏读：
 *   - 一次较慢的同步可能带回比本地乐观写入更旧的版本，直接覆盖会把用户刚读过的
 *     通知打回未读；
 *   - 服务端的已读记录只增不减，因此「取大」与服务端真源不冲突。
 */
export function mergeReadMap(
  ...maps: Array<NotificationReadMap | undefined | null>
): NotificationReadMap {
  const merged: NotificationReadMap = {}
  for (const map of maps) {
    for (const [id, version] of safeEntries(map)) {
      const normalized = normalizeVersion(version)
      if (normalized <= 0) {
        continue
      }
      merged[id] = Math.max(merged[id] ?? 0, normalized)
    }
  }
  return merged
}

/**
 * A notice is unread when its single-notice version is newer than the read
 * marker. The global version deliberately does not participate.
 */
export function isNotificationUnread(
  notice: NotificationMarker,
  state: NotificationState
): boolean {
  if (!notice?.id || state.suppressed?.[notice.id]) {
    return false
  }
  const version = normalizeVersion(notice.version)
  if (version <= 0) {
    return false
  }
  return version > (state.read?.[notice.id] ?? 0)
}

/** Preserve server order (newest first) and never queue the same id twice. */
export function collectUnreadNotifications<T extends NotificationMarker>(
  notices: readonly T[],
  state: NotificationState
): T[] {
  const seen = new Set<string>()
  const pending: T[] = []
  for (const notice of notices) {
    if (!notice?.id || seen.has(notice.id)) {
      continue
    }
    seen.add(notice.id)
    if (isNotificationUnread(notice, state)) {
      pending.push(notice)
    }
  }
  return pending
}

/**
 * 乐观写入：把该通知标记为已读，返回新的状态对象。
 *
 * state.read 通常是账号共享的响应式映射（横幅 / 收件页 / 资料页共用同一份），
 * 所以这里就地写入并原样返回该映射：所有视图会同时看到这条通知不再算未读。
 * 若这份映射属于某个账号，本次写入同时记进该账号的待确认集合，
 * 下一次同步重算映射时不会被冲掉；真正的版本对齐交给 saveNotificationState
 * （写穿服务端）与 confirmReadMark。
 */
export function markNotificationRead<T extends NotificationMarker>(
  state: NotificationState,
  notice: T
): NotificationState {
  const version = normalizeVersion(notice.version)
  if (!notice.id || version <= 0) {
    return state
  }
  state.read[notice.id] = Math.max(state.read?.[notice.id] ?? 0, version)
  const owner = accountKeyByReadMap.get(toRaw(state.read))
  if (owner) {
    const account = accountNotificationState(owner)
    account.pendingRead[notice.id] = Math.max(account.pendingRead[notice.id] ?? 0, version)
  }
  return {
    ...state,
    read: state.read
  }
}

/** 「不再显示」只按 id 写入 suppressed，不依赖版本号，编辑后仍保持隐藏。 */
export function suppressNotification<T extends NotificationMarker>(
  state: NotificationState,
  notice: T
): NotificationState {
  if (!notice.id) {
    return state
  }
  return {
    ...state,
    suppressed: {
      ...(state.suppressed ?? {}),
      [notice.id]: true
    }
  }
}

/** 仅记录服务端全局版本，不触碰 read / suppressed。 */
export function withLastGlobalVersion(
  state: NotificationState,
  version: unknown
): NotificationState {
  return {
    ...state,
    lastGlobalVersion: normalizeGlobalVersion(version)
  }
}
