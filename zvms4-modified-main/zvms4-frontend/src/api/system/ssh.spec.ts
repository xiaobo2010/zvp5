import { expect, it } from 'vitest'
import { buildSshWsUrlFor } from './ssh'

it('keeps a relative API base on the content origin', () => {
  expect(buildSshWsUrlFor('/api', 'one-time', 'https://content.example.test')).toBe(
    'wss://content.example.test/api/system/ssh/ws?ticket=one-time'
  )
})

it('uses the absolute API origin instead of the content origin', () => {
  expect(
    buildSshWsUrlFor('https://api.example.test/api', 'one-time', 'https://content.example.test/app')
  ).toBe('wss://api.example.test/api/system/ssh/ws?ticket=one-time')
})
