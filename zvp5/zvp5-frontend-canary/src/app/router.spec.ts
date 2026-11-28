import { describe, expect, it } from 'vitest'
import { router } from './router'

function metaOf(name: string) {
  const route = router.getRoutes().find((item) => item.name === name)
  expect(route, `route ${name} should exist`).toBeTruthy()
  return route?.meta ?? {}
}

describe('manage route authorization', () => {
  it('keeps the review queue and roster pages manager-only', () => {
    expect(metaOf('manage').roles).toEqual(['admin', 'department'])
    expect(metaOf('manage-users').roles).toEqual(['admin', 'department'])
    expect(metaOf('manage-groups').roles).toEqual(['admin', 'department'])
  })

  it('guards notification publishing by role or notify.publish feature', () => {
    const meta = metaOf('manage-notifications')
    expect(meta.roles).toEqual(['admin'])
    expect(meta.feature).toBe('notify.publish')
  })

  it('guards the audit log by role or logs.read feature', () => {
    const meta = metaOf('manage-logs')
    expect(meta.roles).toEqual(['admin'])
    expect(meta.feature).toBe('logs.read')
  })
})
