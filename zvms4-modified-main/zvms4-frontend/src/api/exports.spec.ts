import { describe, expect, it, vi, beforeEach } from 'vitest'

const axiosMock = vi.hoisted(() => vi.fn())
const downloadBlobMock = vi.hoisted(() => vi.fn())

vi.mock('@/plugins', () => ({ axios: axiosMock }))
vi.mock('@/utils/download', () => ({ downloadBlob: downloadBlobMock }))

import { download as downloadExport, exportFileName } from '@/api/exports.ts'

describe('exportFileName', () => {
  beforeEach(() => {
    axiosMock.mockReset()
    downloadBlobMock.mockReset()
  })

  it('空名兜底成 export-时间戳，并保留扩展名', () => {
    expect(exportFileName('', 'csv')).toMatch(/^export-\d{8}-\d{6}\.csv$/)
  })

  it('未提供名或仅空白也走兜底', () => {
    expect(exportFileName(undefined, 'json')).toMatch(/^export-\d{8}-\d{6}\.json$/)
    expect(exportFileName('   ', 'html')).toMatch(/^export-\d{8}-\d{6}\.html$/)
  })

  it('有名字时沿用原名', () => {
    expect(exportFileName('2026 年数据导出', 'csv')).toBe('2026 年数据导出.csv')
  })

  it('excel/latex 扩展名映射为 xlsx/tex', () => {
    expect(exportFileName('a', 'excel')).toBe('a.xlsx')
    expect(exportFileName('a', 'latex')).toBe('a.tex')
  })

  it('download 用兜底名调用 downloadBlob', async () => {
    axiosMock.mockResolvedValue({ data: new Blob(['x']) })
    await downloadExport('task-1', '', 'csv')
    expect(downloadBlobMock).toHaveBeenCalledTimes(1)
    expect(downloadBlobMock.mock.calls[0][2]).toMatch(/^export-\d{8}-\d{6}\.csv$/)
  })
})
