import { describe, expect, it } from 'vitest'
import {
  collectUnreadNotifications,
  emptyNotificationState,
  isNotificationUnread,
  loadNotificationState,
  markNotificationRead,
  notificationStateKey,
  parseNotificationState,
  saveNotificationState,
  serializeNotificationState,
  suppressNotification,
  withLastGlobalVersion,
  type NotificationState,
  type NotificationStateStorage
} from './notificationState'

function memoryStorage(initial: Record<string, string> = {}): NotificationStateStorage & {
  values: Record<string, string>
} {
  const values = { ...initial }
  return {
    values,
    getItem(key) {
      return key in values ? values[key]! : null
    },
    setItem(key, value) {
      values[key] = value
    }
  }
}

function stateOf(partial: Partial<NotificationState>): NotificationState {
  return {
    ...emptyNotificationState(),
    ...partial
  }
}

describe('notification local state', () => {
  it('binds the storage key to the account unique id', () => {
    expect(notificationStateKey('507f1f77bcf86cd799439011')).toBe(
      'notification-state:507f1f77bcf86cd799439011'
    )
    expect(notificationStateKey('')).toBe('')
    expect(notificationStateKey(null)).toBe('')
  })

  it('keeps read and suppressed markers isolated per account', () => {
    const storage = memoryStorage()
    const accountA = stateOf({
      read: { 'notice-a': 2 },
      suppressed: { 'notice-old': true }
    })
    const accountB = stateOf({})

    expect(saveNotificationState('account-a', accountA, storage)).toBe(true)
    expect(saveNotificationState('account-b', accountB, storage)).toBe(true)

    expect(loadNotificationState('account-a', storage)).toEqual(accountA)
    expect(loadNotificationState('account-b', storage)).toEqual(accountB)
    expect(storage.values['notification-state:account-a']).toBeTruthy()
    expect(storage.values['notification-state:account-b']).toBeTruthy()
  })

  it('treats a new notice as unread even when the cached global version equals the server', () => {
    // 新用户第一次缓存：本地 lastGlobalVersion 可能已被写成 7，但没有单条 read。
    const state = withLastGlobalVersion(emptyNotificationState(), 7)
    expect(isNotificationUnread({ id: 'notice-1', version: 1 }, state)).toBe(true)
  })

  it('does not re-prompt for the same version after read, but does after an edit bumps it', () => {
    const notice = { id: 'notice-1', version: 1 }
    const read = markNotificationRead(emptyNotificationState(), notice)

    expect(isNotificationUnread(notice, read)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-1', version: 2 }, read)).toBe(true)
  })

  it('permanently suppresses by id regardless of later version increases', () => {
    const suppressed = suppressNotification(emptyNotificationState(), {
      id: 'notice-1',
      version: 1
    })

    expect(isNotificationUnread({ id: 'notice-1', version: 1 }, suppressed)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-1', version: 99 }, suppressed)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-2', version: 1 }, suppressed)).toBe(true)
  })

  it('read only updates read, suppress only updates suppressed', () => {
    const base = withLastGlobalVersion(emptyNotificationState(), 12)

    const read = markNotificationRead(base, { id: 'notice-1', version: 3 })
    expect(read.read).toEqual({ 'notice-1': 3 })
    expect(read.suppressed).toEqual({})
    expect(read.lastGlobalVersion).toBe(12)

    const suppressed = suppressNotification(base, { id: 'notice-2', version: 4 })
    expect(suppressed.read).toEqual({})
    expect(suppressed.suppressed).toEqual({ 'notice-2': true })
    expect(suppressed.lastGlobalVersion).toBe(12)
  })

  it('collects unread notices in server order without duplicate ids', () => {
    const state = stateOf({
      read: { 'notice-read': 1 },
      suppressed: { 'notice-hidden': true }
    })
    const notices = [
      { id: 'notice-new', version: 1 },
      { id: 'notice-read', version: 1 },
      { id: 'notice-new', version: 2 },
      { id: 'notice-hidden', version: 5 }
    ]

    expect(collectUnreadNotifications(notices, state)).toEqual([{ id: 'notice-new', version: 1 }])
  })

  it('degrades corrupt JSON and hostile keys to a safe empty state', () => {
    expect(parseNotificationState('{not json')).toEqual(emptyNotificationState())
    expect(parseNotificationState('null')).toEqual(emptyNotificationState())

    const parsed = parseNotificationState(
      '{"lastGlobalVersion":4,"read":{"__proto__":9,"good":2,"bad":"x"},"suppressed":{"constructor":true,"keep":true,"no":1}}'
    )
    expect(parsed.lastGlobalVersion).toBe(4)
    expect(parsed.read).toEqual({ good: 2 })
    expect(parsed.suppressed).toEqual({ keep: true })
  })

  it('survives localStorage being unavailable or throwing', () => {
    const throwing: NotificationStateStorage = {
      getItem() {
        throw new Error('blocked')
      },
      setItem() {
        throw new Error('quota')
      }
    }

    expect(loadNotificationState('account-a', throwing)).toEqual(emptyNotificationState())
    expect(saveNotificationState('account-a', emptyNotificationState(), throwing)).toBe(false)
    expect(loadNotificationState('account-a', null)).toEqual(emptyNotificationState())
    expect(saveNotificationState('account-a', emptyNotificationState(), null)).toBe(false)
  })

  it('serializes only normalized marker data', () => {
    const state = stateOf({
      lastGlobalVersion: 3.9,
      read: { a: 2.8 },
      suppressed: { b: true }
    })
    expect(JSON.parse(serializeNotificationState(state))).toEqual({
      lastGlobalVersion: 3,
      read: { a: 2 },
      suppressed: { b: true }
    })
    expect(parseNotificationState(serializeNotificationState(state))).toEqual(
      stateOf({
        lastGlobalVersion: 3,
        read: { a: 2 },
        suppressed: { b: true }
      })
    )
  })
})
