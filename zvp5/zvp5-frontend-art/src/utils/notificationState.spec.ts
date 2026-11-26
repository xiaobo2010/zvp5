import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  accountNotificationState,
  applyServerReadMap,
  bindAccountNotificationState,
  clearSentReadMark,
  collectUnreadNotifications,
  confirmReadMark,
  dropPendingReadMark,
  effectiveReadMap,
  emptyNotificationState,
  isNotificationUnread,
  loadLegacyReadMap,
  loadNotificationState,
  markNotificationRead,
  mergeReadMap,
  notificationStateKey,
  parseNotificationState,
  pendingServerWrites,
  queueReadMark,
  resetAccountNotificationState,
  rollbackReadMark,
  saveNotificationState,
  serializeNotificationState,
  setReadWriteHandler,
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

const ACCOUNT = '507f1f77bcf86cd799439011'

beforeEach(() => {
  // 账号级内存态是模块级单例：每个用例前清干净，避免互相串数据。
  resetAccountNotificationState(ACCOUNT)
  resetAccountNotificationState('account-b')
})

describe('notification local state', () => {
  it('binds the storage key to the account unique id', () => {
    expect(notificationStateKey(ACCOUNT)).toBe(`notification-state:${ACCOUNT}`)
    expect(notificationStateKey('')).toBe('')
    expect(notificationStateKey(null)).toBe('')
  })

  it('keeps suppressed markers isolated per account', () => {
    const storage = memoryStorage()
    const accountA = stateOf({ suppressed: { 'notice-old': true } })
    const accountB = stateOf({})

    expect(saveNotificationState('account-a', accountA, storage)).toBe(true)
    expect(saveNotificationState('account-b', accountB, storage)).toBe(true)

    expect(loadNotificationState('account-a', storage).suppressed).toEqual({
      'notice-old': true
    })
    expect(loadNotificationState('account-b', storage).suppressed).toEqual({})
  })

  it('persists only the device-level preferences and the global version', () => {
    const storage = memoryStorage()
    const state = stateOf({
      lastGlobalVersion: 9,
      read: { 'notice-1': 2 },
      suppressed: { 'notice-2': true }
    })

    saveNotificationState(ACCOUNT, state, storage)

    const persisted = JSON.parse(storage.values[`notification-state:${ACCOUNT}`])
    expect(persisted.lastGlobalVersion).toBe(9)
    expect(persisted.suppressed).toEqual({ 'notice-2': true })
    // 已读的真源在服务端，本地不再落已读记录。
    expect(persisted.read).toBeUndefined()
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

    expect(loadNotificationState(ACCOUNT, throwing)).toEqual(emptyNotificationState())
    expect(saveNotificationState(ACCOUNT, emptyNotificationState(), throwing)).toBe(false)
    expect(loadNotificationState(ACCOUNT, null)).toEqual(emptyNotificationState())
    expect(saveNotificationState(ACCOUNT, emptyNotificationState(), null)).toBe(false)
  })

  it('serializes only normalized marker data', () => {
    const state = stateOf({
      lastGlobalVersion: 3.9,
      suppressed: { b: true }
    })
    expect(JSON.parse(serializeNotificationState(state))).toEqual({
      lastGlobalVersion: 3,
      suppressed: { b: true }
    })
  })
})

describe('已读以服务端为准', () => {
  it('marks a notice unread until the server read map covers its version', () => {
    bindAccountNotificationState(ACCOUNT)
    const notice = { id: 'notice-1', version: 2 }

    expect(isNotificationUnread(notice, loadNotificationState(ACCOUNT))).toBe(true)

    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })
    expect(isNotificationUnread(notice, loadNotificationState(ACCOUNT))).toBe(false)
  })

  it('re-opens a notice after the server bumps its version', () => {
    bindAccountNotificationState(ACCOUNT)
    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })

    // 管理员编辑了这条通知：版本 +1，之前那条已读记录不再覆盖新版本。
    expect(
      isNotificationUnread({ id: 'notice-1', version: 3 }, loadNotificationState(ACCOUNT))
    ).toBe(true)
  })

  it('merges the server map without downgrading a newer local write', () => {
    bindAccountNotificationState(ACCOUNT)
    queueReadMark(ACCOUNT, 'notice-1', 3)

    // 一次较慢的同步带回更旧的版本：取大，不把刚写入的通知打回未读。
    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })
    expect(effectiveReadMap(ACCOUNT)['notice-1']).toBe(3)
    expect(
      isNotificationUnread({ id: 'notice-1', version: 3 }, loadNotificationState(ACCOUNT))
    ).toBe(false)
  })

  it('drops a pending write once the server confirms it', () => {
    bindAccountNotificationState(ACCOUNT)
    queueReadMark(ACCOUNT, 'notice-1', 2)
    expect(pendingServerWrites(ACCOUNT)).toEqual([{ id: 'notice-1', version: 2 }])

    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })
    expect(pendingServerWrites(ACCOUNT)).toEqual([])
  })

  it('rolls a failed write back to the previous version', () => {
    bindAccountNotificationState(ACCOUNT)
    applyServerReadMap(ACCOUNT, { 'notice-1': 1 })

    queueReadMark(ACCOUNT, 'notice-1', 2)
    rollbackReadMark(ACCOUNT, 'notice-1', 1)

    expect(effectiveReadMap(ACCOUNT)['notice-1']).toBe(1)
    // 回滚后这条通知重新变未读，下次检查会再次提醒。
    expect(
      isNotificationUnread({ id: 'notice-1', version: 2 }, loadNotificationState(ACCOUNT))
    ).toBe(true)
  })

  it('confirms a server receipt as the new read version', () => {
    bindAccountNotificationState(ACCOUNT)
    queueReadMark(ACCOUNT, 'notice-1', 2)

    confirmReadMark(ACCOUNT, 'notice-1', 2)
    expect(effectiveReadMap(ACCOUNT)['notice-1']).toBe(2)
    expect(pendingServerWrites(ACCOUNT)).toEqual([])
  })

  it('forgets a pending write for a notification that no longer exists', () => {
    bindAccountNotificationState(ACCOUNT)
    queueReadMark(ACCOUNT, 'gone', 1)

    dropPendingReadMark(ACCOUNT, 'gone')
    expect(pendingServerWrites(ACCOUNT)).toEqual([])
  })

  it('keeps read state per account', () => {
    bindAccountNotificationState(ACCOUNT)
    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })

    bindAccountNotificationState('account-b')
    expect(effectiveReadMap('account-b')).toEqual({})
  })

  it('discards the previous account state when switching accounts', () => {
    bindAccountNotificationState(ACCOUNT)
    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })

    resetAccountNotificationState(ACCOUNT)
    expect(effectiveReadMap(ACCOUNT)).toEqual({})
  })
})

describe('未读判定', () => {
  it('treats a new notice as unread even when the cached global version equals the server', () => {
    const state = withLastGlobalVersion(emptyNotificationState(), 7)
    expect(isNotificationUnread({ id: 'notice-1', version: 1 }, state)).toBe(true)
  })

  it('does not re-prompt for the same version after read, but does after an edit bumps it', () => {
    const notice = { id: 'notice-1', version: 1 }
    const read = markNotificationRead(emptyNotificationState(), notice)

    expect(isNotificationUnread(notice, read)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-1', version: 2 }, read)).toBe(true)
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
})

describe('「不再显示」仍只在本机', () => {
  it('permanently suppresses by id regardless of later version increases', () => {
    const suppressed = suppressNotification(emptyNotificationState(), {
      id: 'notice-1',
      version: 1
    })

    expect(isNotificationUnread({ id: 'notice-1', version: 1 }, suppressed)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-1', version: 99 }, suppressed)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-2', version: 1 }, suppressed)).toBe(true)
  })

  it('suppress only touches suppressed, read only touches read', () => {
    // markNotificationRead 就地写入共享的 read 映射，所以这里用两份互不相干的基础状态。
    const read = markNotificationRead(
      withLastGlobalVersion(emptyNotificationState(), 12),
      { id: 'notice-1', version: 3 }
    )
    expect(read.read).toEqual({ 'notice-1': 3 })
    expect(read.suppressed).toEqual({})
    expect(read.lastGlobalVersion).toBe(12)

    const suppressed = suppressNotification(
      withLastGlobalVersion(emptyNotificationState(), 12),
      { id: 'notice-2', version: 4 }
    )
    expect(suppressed.read).toEqual({})
    expect(suppressed.suppressed).toEqual({ 'notice-2': true })
    expect(suppressed.lastGlobalVersion).toBe(12)
  })

  it('keeps the suppression when the notice list is re-read from the server', () => {
    const storage = memoryStorage()
    saveNotificationState(
      ACCOUNT,
      stateOf({ suppressed: { 'notice-1': true }, lastGlobalVersion: 3 }),
      storage
    )

    bindAccountNotificationState(ACCOUNT, storage)
    const state = loadNotificationState(ACCOUNT, storage)
    expect(state.suppressed['notice-1']).toBe(true)
    expect(isNotificationUnread({ id: 'notice-1', version: 1 }, state)).toBe(false)
  })
})

describe('已读写穿', () => {
  it('queues a local write and hands it to the write-through handler', () => {
    const storage = memoryStorage()
    bindAccountNotificationState(ACCOUNT, storage)

    const written: Array<[string, number]> = []
    const restore = setReadWriteHandler((_account, id, version) => {
      written.push([id, version])
    })

    const state = loadNotificationState(ACCOUNT, storage)
    const next = markNotificationRead(state, { id: 'notice-1', version: 2 })
    saveNotificationState(ACCOUNT, next, storage)

    expect(written).toEqual([['notice-1', 2]])
    expect(pendingServerWrites(ACCOUNT)).toEqual([{ id: 'notice-1', version: 2 }])
    restore()
  })

  it('does not write the same read twice', () => {
    const storage = memoryStorage()
    bindAccountNotificationState(ACCOUNT, storage)

    const written: Array<[string, number]> = []
    const restore = setReadWriteHandler((_account, id, version) => {
      written.push([id, version])
    })

    const state = loadNotificationState(ACCOUNT, storage)
    const next = markNotificationRead(state, { id: 'notice-1', version: 2 })
    saveNotificationState(ACCOUNT, next, storage)
    saveNotificationState(ACCOUNT, next, storage)

    expect(written).toEqual([['notice-1', 2]])
    restore()
  })

  it('does not re-write a read the server already confirmed', () => {
    const storage = memoryStorage()
    bindAccountNotificationState(ACCOUNT, storage)
    applyServerReadMap(ACCOUNT, { 'notice-1': 2 })

    const written: Array<[string, number]> = []
    const restore = setReadWriteHandler((_account, id, version) => {
      written.push([id, version])
    })

    const state = loadNotificationState(ACCOUNT, storage)
    saveNotificationState(ACCOUNT, state, storage)

    expect(written).toEqual([])
    restore()
  })

  it('allows a retry after a failed write-through', () => {
    const storage = memoryStorage()
    bindAccountNotificationState(ACCOUNT, storage)

    const written: Array<[string, number]> = []
    const restore = setReadWriteHandler((_account, id, version) => {
      written.push([id, version])
    })

    const state = loadNotificationState(ACCOUNT, storage)
    const next = markNotificationRead(state, { id: 'notice-1', version: 2 })
    saveNotificationState(ACCOUNT, next, storage)
    // 写穿失败：清掉「已发出」标记。
    clearSentReadMark(ACCOUNT, 'notice-1')
    saveNotificationState(ACCOUNT, next, storage)

    expect(written).toEqual([
      ['notice-1', 2],
      ['notice-1', 2]
    ])
    restore()
  })

  it('shares one live read map across every surface of an account', () => {
    const storage = memoryStorage()
    bindAccountNotificationState(ACCOUNT, storage)

    const first = loadNotificationState(ACCOUNT, storage)
    const second = loadNotificationState(ACCOUNT, storage)
    expect(first.read).toBe(second.read)

    // 一处标记已读，另一处立刻看到（横幅不会再重复弹这条）。
    markNotificationRead(first, { id: 'notice-1', version: 2 })
    expect(isNotificationUnread({ id: 'notice-1', version: 2 }, second)).toBe(false)
    expect(isNotificationUnread({ id: 'notice-1', version: 3 }, second)).toBe(true)
  })
})

describe('历史已读迁移', () => {
  it('reads the pre-cloud read markers and queues them for the server', () => {
    const storage = memoryStorage({
      [`notification-state:${ACCOUNT}`]: JSON.stringify({
        lastGlobalVersion: 4,
        read: { 'old-notice': 2 },
        suppressed: { 'hidden-notice': true }
      })
    })

    expect(loadLegacyReadMap(ACCOUNT, storage)).toEqual({ 'old-notice': 2 })

    bindAccountNotificationState(ACCOUNT, storage)
    expect(pendingServerWrites(ACCOUNT)).toEqual([{ id: 'old-notice', version: 2 }])
    // 已读过的旧通知不会重新弹一遍。
    expect(
      isNotificationUnread({ id: 'old-notice', version: 2 }, loadNotificationState(ACCOUNT, storage))
    ).toBe(false)
  })
})

describe('mergeReadMap', () => {
  it('takes the larger version for each id and ignores junk', () => {
    expect(mergeReadMap({ a: 1, b: 2 }, { a: 3, c: 0, d: -1 })).toEqual({ a: 3, b: 2 })
  })

  it('ignores hostile keys and non-numeric values', () => {
    expect(mergeReadMap({ __proto__: 9, ok: 2, bad: 'x' } as never)).toEqual({ ok: 2 })
  })
})

describe('账号内存态', () => {
  it('creates an empty state per account', () => {
    const account = accountNotificationState(ACCOUNT)
    expect(account.read).toEqual({})
    expect(account.pendingRead).toEqual({})
    expect(account.serverRead).toEqual({})
  })

  it('only loads the persisted part once per account', () => {
    const storage = memoryStorage({
      [`notification-state:${ACCOUNT}`]: JSON.stringify({ suppressed: { x: true } })
    })

    bindAccountNotificationState(ACCOUNT, storage)
    expect(accountNotificationState(ACCOUNT).suppressed).toEqual({ x: true })

    storage.setItem(`notification-state:${ACCOUNT}`, JSON.stringify({ suppressed: { y: true } }))
    bindAccountNotificationState(ACCOUNT, storage)
    expect(accountNotificationState(ACCOUNT).suppressed).toEqual({ x: true })
  })
})
