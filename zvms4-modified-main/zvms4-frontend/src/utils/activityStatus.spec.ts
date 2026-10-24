import { describe, expect, it } from 'vitest'
import { displayStatus } from './activityStatus'

describe('displayStatus', () => {
  it('reports a pending activity as pending even when the member is effective', () => {
    expect(displayStatus({ status: 'pending', mine: { status: 'effective' } })).toBe('pending')
  })

  it('reports a refused activity as refused even when the member is effective', () => {
    expect(displayStatus({ status: 'refused', mine: { status: 'effective' } })).toBe('refused')
  })

  it('reports an effective activity with an effective member as effective', () => {
    expect(displayStatus({ status: 'effective', mine: { status: 'effective' } })).toBe('effective')
  })

  it('falls back to pending when the member status is missing', () => {
    expect(displayStatus({ status: 'effective' })).toBe('pending')
    expect(displayStatus({ status: 'effective', mine: undefined })).toBe('pending')
    expect(displayStatus({ status: 'effective', mine: { status: undefined } })).toBe('pending')
  })

  it('falls back to pending when the whole row is missing', () => {
    expect(displayStatus(undefined)).toBe('pending')
    expect(displayStatus(null)).toBe('pending')
    expect(displayStatus({})).toBe('pending')
  })

  it('lets the member status decide once the activity is effective', () => {
    expect(displayStatus({ status: 'effective', mine: { status: 'pending' } })).toBe('pending')
    expect(displayStatus({ status: 'effective', mine: { status: 'refused' } })).toBe('refused')
  })

  it('never reports effective for an unknown activity status', () => {
    expect(displayStatus({ mine: { status: 'effective' } })).toBe('pending')
  })
})
