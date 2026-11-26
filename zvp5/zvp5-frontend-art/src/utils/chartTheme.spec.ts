import { expect, it } from 'vitest'
import { resolveChartTheme } from './chartTheme'

it('uses a light chart palette by default', () => {
  expect(resolveChartTheme(false)).toMatchObject({
    text: '#303133',
    grid: '#e4e7ed'
  })
})

it('switches chart text and grid colors in dark mode', () => {
  const dark = resolveChartTheme(true)
  expect(dark.text).not.toBe(resolveChartTheme(false).text)
  expect(dark.grid).not.toBe(resolveChartTheme(false).grid)
})
