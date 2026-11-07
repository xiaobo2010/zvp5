/**
 * 站内通知的本地状态（按账号、按设备持久化）。
 *
 * 服务端不保存每个用户的阅读记录，也不因本地操作推进版本号。这里的所有函数都
 * 不触碰网络，便于单测覆盖「已阅后同版不再弹出 / 编辑增版本重弹 / 永久隐藏」。
 */
export interface NotificationMarker {
  id: string
  version: number
}

export interface NotificationState {
  /** 上次从服务端拿到/处理的全局版本，仅用于诊断和后续刷新优化。 */
  lastGlobalVersion: number
  /** 已阅：记录阅读时的单条通知版本。 */
  read: Record<string, number>
  /** 不再显示：按通知 id 永久压制，编辑导致版本增大也不会重新弹出。 */
  suppressed: Record<string, true>
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

export function emptyNotificationState(): NotificationState {
  return {
    lastGlobalVersion: 0,
    read: {},
    suppressed: {}
  }
}

export function notificationStateKey(accountId: string | number | null | undefined): string {
  const id = String(accountId ?? '').trim()
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

/**
 * Parse a persisted state defensively. Invalid JSON, proto-pollution keys and
 * non-numeric versions all degrade to an empty/partial state rather than
 * breaking app startup.
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

  if (record.read && typeof record.read === 'object' && !Array.isArray(record.read)) {
    for (const [id, version] of Object.entries(record.read as Record<string, unknown>)) {
      if (!id || UNSAFE_KEYS.has(id)) {
        continue
      }
      const normalized = normalizeVersion(version)
      if (normalized > 0) {
        state.read[id] = normalized
      }
    }
  }

  if (
    record.suppressed &&
    typeof record.suppressed === 'object' &&
    !Array.isArray(record.suppressed)
  ) {
    for (const [id, value] of Object.entries(record.suppressed as Record<string, unknown>)) {
      if (!id || UNSAFE_KEYS.has(id)) {
        continue
      }
      if (value === true) {
        state.suppressed[id] = true
      }
    }
  }

  return state
}

export function serializeNotificationState(state: NotificationState): string {
  const read: Record<string, number> = {}
  for (const [id, version] of Object.entries(state.read ?? {})) {
    if (!id || UNSAFE_KEYS.has(id)) {
      continue
    }
    const normalized = normalizeVersion(version)
    if (normalized > 0) {
      read[id] = normalized
    }
  }

  const suppressed: Record<string, true> = {}
  for (const [id, value] of Object.entries(state.suppressed ?? {})) {
    if (!id || UNSAFE_KEYS.has(id) || value !== true) {
      continue
    }
    suppressed[id] = true
  }

  return JSON.stringify({
    lastGlobalVersion: normalizeGlobalVersion(state.lastGlobalVersion),
    read,
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

export function loadNotificationState(
  accountId: string | number | null | undefined,
  storage: NotificationStateStorage | null = defaultStorage()
): NotificationState {
  const key = notificationStateKey(accountId)
  if (!key || !storage) {
    return emptyNotificationState()
  }
  try {
    return parseNotificationState(storage.getItem(key))
  } catch {
    return emptyNotificationState()
  }
}

/** Returns false when localStorage is unavailable or full; callers stay silent. */
export function saveNotificationState(
  accountId: string | number | null | undefined,
  state: NotificationState,
  storage: NotificationStateStorage | null = defaultStorage()
): boolean {
  const key = notificationStateKey(accountId)
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
 * A notice is unread when its single-notice version is newer than the local
 * read marker. The global version deliberately does not participate.
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

/** 「已阅」只写 read 标记，不推进全局版本。 */
export function markNotificationRead<T extends NotificationMarker>(
  state: NotificationState,
  notice: T
): NotificationState {
  const version = normalizeVersion(notice.version)
  if (!notice.id || version <= 0) {
    return state
  }
  return {
    ...state,
    read: {
      ...(state.read ?? {}),
      [notice.id]: version
    }
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
