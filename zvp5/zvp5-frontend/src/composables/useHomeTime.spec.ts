import { effectScope, nextTick, ref } from 'vue'
import type { EffectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { UserActivityTimeSums } from '@/../types'
import { useHomeTime } from './useHomeTime'

type TimeIO = {
  read: (id: string) => Promise<UserActivityTimeSums>
  save: (value: UserActivityTimeSums) => void
}

const UNKNOWN_TIME_ERROR = '暂时无法取得时长，请重试。'
const REFRESH_TIME_ERROR = '刷新失败，正在显示上次成功取得的时长。'
const KNOWN_TIME: UserActivityTimeSums = {
  'on-campus': 1.25,
  'off-campus': 2.5,
  'social-practice': 3.75
}

const scopes: EffectScope[] = []

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function createHarness(read: TimeIO['read'], key = 'user-a') {
  const userKey = ref(key)
  const save = vi.fn((_value: UserActivityTimeSums) => undefined)
  const scope = effectScope()
  scopes.push(scope)
  const state = scope.run(() => useHomeTime(() => userKey.value, { read, save }))
  if (!state) throw new Error('Failed to create useHomeTime scope')
  return { state, userKey, save, scope }
}

afterEach(() => {
  for (const scope of scopes.splice(0)) scope.stop()
})

describe('useHomeTime', () => {
  it('keeps an unknown first fetch empty while loading and after failure', async () => {
    const request = deferred<UserActivityTimeSums>()
    const read = vi.fn<TimeIO['read']>(() => request.promise)
    const { state, save } = createHarness(read)

    expect(read).toHaveBeenCalledWith('user-a')
    expect(state.loading.value).toBe(true)
    expect(state.raw.value).toBeNull()
    expect(state.sums.value).toBeUndefined()
    expect(state.progress.value).toBeNull()

    request.reject(new Error('offline'))
    await expect(request.promise).rejects.toThrow('offline')
    await nextTick()

    expect(state.loading.value).toBe(false)
    expect(state.raw.value).toBeNull()
    expect(state.sums.value).toBeUndefined()
    expect(state.progress.value).toBeNull()
    expect(state.error.value).toBe(UNKNOWN_TIME_ERROR)
    expect(save).not.toHaveBeenCalled()
  })

  it('publishes a valid response in all supported shapes', async () => {
    const read = vi.fn<TimeIO['read']>(() => Promise.resolve(KNOWN_TIME))
    const { state, save } = createHarness(read)

    await vi.waitFor(() => expect(state.raw.value).not.toBeNull())

    expect(state.raw.value).toEqual({
      onCampus: 1.25,
      offCampus: 2.5,
      socialPractice: 3.75
    })
    expect(state.sums.value).toEqual(KNOWN_TIME)
    expect(state.progress.value?.total).toBe(7.5)
    expect(state.loading.value).toBe(false)
    expect(state.error.value).toBe('')
    expect(save).toHaveBeenCalledOnce()
    expect(save).toHaveBeenCalledWith(KNOWN_TIME)
  })

  it.each([
    null,
    {},
    { ...KNOWN_TIME, 'on-campus': '1.25' },
    { ...KNOWN_TIME, 'off-campus': Number.NaN },
    { ...KNOWN_TIME, 'social-practice': Number.POSITIVE_INFINITY },
    { ...KNOWN_TIME, 'on-campus': -1 }
  ])('rejects a malformed initial response without inventing zero data', async (payload) => {
    const read = vi.fn<TimeIO['read']>(() => Promise.resolve(payload as UserActivityTimeSums))
    const { state, save } = createHarness(read)

    await vi.waitFor(() => expect(state.error.value).toBe(UNKNOWN_TIME_ERROR))

    expect(state.loading.value).toBe(false)
    expect(state.raw.value).toBeNull()
    expect(state.sums.value).toBeUndefined()
    expect(state.progress.value).toBeNull()
    expect(save).not.toHaveBeenCalled()
  })

  it('retains known data when a refresh request fails', async () => {
    const read = vi
      .fn<TimeIO['read']>()
      .mockResolvedValueOnce(KNOWN_TIME)
      .mockRejectedValueOnce(new Error('offline'))
    const { state, save } = createHarness(read)

    await vi.waitFor(() => expect(state.raw.value).not.toBeNull())
    const knownRaw = state.raw.value

    await state.refresh()

    expect(state.raw.value).toBe(knownRaw)
    expect(state.sums.value).toEqual(KNOWN_TIME)
    expect(state.progress.value?.total).toBe(7.5)
    expect(state.loading.value).toBe(false)
    expect(state.error.value).toBe(REFRESH_TIME_ERROR)
    expect(read).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenCalledOnce()
  })

  it('retains known data when a refresh response is malformed', async () => {
    const read = vi
      .fn<TimeIO['read']>()
      .mockResolvedValueOnce(KNOWN_TIME)
      .mockResolvedValueOnce({
        ...KNOWN_TIME,
        'off-campus': null
      } as unknown as UserActivityTimeSums)
    const { state, save } = createHarness(read)

    await vi.waitFor(() => expect(state.raw.value).not.toBeNull())
    const knownRaw = state.raw.value

    await state.refresh()

    expect(state.raw.value).toBe(knownRaw)
    expect(state.sums.value).toEqual(KNOWN_TIME)
    expect(state.progress.value?.total).toBe(7.5)
    expect(state.error.value).toBe(REFRESH_TIME_ERROR)
    expect(save).toHaveBeenCalledOnce()
  })

  it('ignores late results from a previous account and only saves the active one', async () => {
    const first = deferred<UserActivityTimeSums>()
    const second = deferred<UserActivityTimeSums>()
    const read = vi.fn<TimeIO['read']>((id) => (id === 'user-a' ? first.promise : second.promise))
    const { state, userKey, save } = createHarness(read)
    const nextTime: UserActivityTimeSums = {
      'on-campus': 8,
      'off-campus': 9,
      'social-practice': 10
    }

    expect(read).toHaveBeenCalledWith('user-a')

    userKey.value = 'user-b'

    expect(read).toHaveBeenCalledWith('user-b')
    expect(state.raw.value).toBeNull()
    expect(state.sums.value).toBeUndefined()
    expect(state.progress.value).toBeNull()
    expect(state.loading.value).toBe(true)

    second.resolve(nextTime)
    await second.promise
    await nextTick()

    expect(state.raw.value).toEqual({
      onCampus: 8,
      offCampus: 9,
      socialPractice: 10
    })
    expect(save).toHaveBeenCalledOnce()
    expect(save).toHaveBeenCalledWith(nextTime)

    first.resolve(KNOWN_TIME)
    await first.promise
    await nextTick()

    expect(state.raw.value).toEqual({
      onCampus: 8,
      offCampus: 9,
      socialPractice: 10
    })
    expect(state.error.value).toBe('')
    expect(save).toHaveBeenCalledOnce()
  })

  it('does not read when there is no active account', () => {
    const read = vi.fn<TimeIO['read']>()
    const { state } = createHarness(read, '')

    expect(read).not.toHaveBeenCalled()
    expect(state.loading.value).toBe(false)
    expect(state.raw.value).toBeNull()
    expect(state.error.value).toBe('')
  })

  it('does not publish or save a result after scope disposal', async () => {
    const request = deferred<UserActivityTimeSums>()
    const read = vi.fn<TimeIO['read']>(() => request.promise)
    const { state, save, scope } = createHarness(read)

    scope.stop()
    request.resolve(KNOWN_TIME)
    await request.promise
    await nextTick()

    expect(state.raw.value).toBeNull()
    expect(state.sums.value).toBeUndefined()
    expect(state.progress.value).toBeNull()
    expect(state.error.value).toBe('')
    expect(save).not.toHaveBeenCalled()
  })
})
