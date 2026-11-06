import { expect, it } from 'vitest'
import { validateOptionalProductionRaceApiBase, validateProductionApiBase } from './apiBaseConfig'

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
