import { expect, it } from 'vitest'
import {
  resolveContentApiBase,
  validateOptionalContentApiBase,
  validateOptionalProductionRaceApiBase,
  validateProductionApiBase
} from './apiBaseConfig'

it('accepts an absolute HTTPS API base ending in /api', () => {
  expect(validateProductionApiBase('https://api.example.test/api')).toBe(
    'https://api.example.test/api'
  )
})

it('rejects a missing API base', () => {
  expect(() => validateProductionApiBase('')).toThrow('VITE_API_BASE is required')
})

it('rejects a relative API base', () => {
  expect(() => validateProductionApiBase('/api')).toThrow('must be an absolute URL')
})

it('rejects a non-HTTPS API base', () => {
  expect(() => validateProductionApiBase('http://api.example.test/api')).toThrow(
    'absolute HTTPS URL ending in /api'
  )
})

it('rejects an API base without the /api path', () => {
  expect(() => validateProductionApiBase('https://api.example.test')).toThrow(
    'absolute HTTPS URL ending in /api'
  )
})

it('accepts an optional absolute race API base', () => {
  expect(validateOptionalProductionRaceApiBase('https://race.example.test/api')).toBe(
    'https://race.example.test/api'
  )
})

it('treats a missing optional race API base as disabled', () => {
  expect(validateOptionalProductionRaceApiBase('')).toBeUndefined()
  expect(validateOptionalProductionRaceApiBase(undefined)).toBeUndefined()
})

it('rejects an invalid configured race API base', () => {
  expect(() => validateOptionalProductionRaceApiBase('https://race.example.test')).toThrow(
    'VITE_API_RACE_BASE must be an absolute HTTPS URL ending in /api'
  )
})

it('treats a missing optional content API base as "derive from the page origin"', () => {
  expect(validateOptionalContentApiBase('')).toBeUndefined()
  expect(validateOptionalContentApiBase(undefined)).toBeUndefined()
})

it('accepts an absolute content API base', () => {
  expect(validateOptionalContentApiBase('https://content.example.test/api')).toBe(
    'https://content.example.test/api'
  )
})

it('rejects a content API base without the /api path', () => {
  expect(() => validateOptionalContentApiBase('https://content.example.test')).toThrow(
    'VITE_API_CONTENT_BASE must be an absolute HTTPS URL ending in /api'
  )
})

it('derives the content base from the page origin when unset', () => {
  expect(resolveContentApiBase(undefined, 'https://zvp.example.test')).toBe(
    'https://zvp.example.test/api'
  )
  expect(resolveContentApiBase('', 'https://canary.example.test')).toBe(
    'https://canary.example.test/api'
  )
})

it('falls back to a relative /api without a page origin (SSR / unit tests)', () => {
  expect(resolveContentApiBase(undefined, undefined)).toBe('/api')
})

it('prefers the configured content base and strips trailing slashes', () => {
  expect(resolveContentApiBase('https://zvp.example.test/api/', 'https://canary.example.test')).toBe(
    'https://zvp.example.test/api'
  )
})
