import { beforeEach, describe, expect, it } from 'vitest'
import { clearFlights, joinFlight, pendingFlightCount } from './singleFlight'

beforeEach(() => {
  clearFlights()
})

describe('在途合并（single-flight）', () => {
  it('gives the same promise to everyone joining the same key', async () => {
    const leader = joinFlight<string>('/api/v3/users/me/device')
    const follower = joinFlight<string>('/api/v3/users/me/device')

    expect(leader.leader).toBe(true)
    expect(follower.leader).toBe(false)
    expect(follower.promise).toBe(leader.promise)

    leader.settle('ok')
    await expect(follower.promise).resolves.toBe('ok')
  })

  it('keeps different keys apart', () => {
    const first = joinFlight<string>('/api/v3/users/me/device')
    const second = joinFlight<string>('/api/v3/users/me/device?x=1')

    expect(second.leader).toBe(true)
    expect(second.promise).not.toBe(first.promise)
    expect(pendingFlightCount()).toBe(2)
  })

  it('broadcasts a failure to the followers', async () => {
    const leader = joinFlight<string>('k')
    const follower = joinFlight<string>('k')
    const failure = new Error('boom')

    leader.fail(failure)

    await expect(follower.promise).rejects.toBe(failure)
    expect(pendingFlightCount()).toBe(0)
  })

  it('ends the flight once it settles', async () => {
    const leader = joinFlight<string>('k')
    leader.settle('first')
    leader.settle('second')

    await expect(leader.promise).resolves.toBe('first')
    expect(pendingFlightCount()).toBe(0)
  })

  it('starts a fresh flight after the previous one ended', async () => {
    const first = joinFlight<string>('k')
    first.settle('done')
    await first.promise

    const second = joinFlight<string>('k')
    expect(second.leader).toBe(true)
    expect(second.promise).not.toBe(first.promise)
  })

  it('does not raise an unhandled rejection when nobody follows a failed flight', async () => {
    const leader = joinFlight<string>('lonely')
    leader.fail(new Error('boom'))
    await expect(leader.promise).rejects.toThrow('boom')
  })
})
