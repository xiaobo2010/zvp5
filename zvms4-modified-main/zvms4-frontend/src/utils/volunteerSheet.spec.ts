import { describe, expect, it } from 'vitest'
import * as XLSX from 'xlsx'
import { normalizeVolunteerSheetRows, parseVolunteerXlsx } from './volunteerSheet'

describe('volunteer sheet rows', () => {
  it('accepts student id then name then duration', () => {
    expect(
      normalizeVolunteerSheetRows([
        ['学号', '姓名', '时长'],
        ['20240001', '张三', '2']
      ])
    ).toEqual([['20240001', '张三', '2']])
  })

  it('accepts name then student id then duration', () => {
    expect(
      normalizeVolunteerSheetRows([
        ['姓名', '学号', '时长'],
        ['张三', '20240001', '2']
      ])
    ).toEqual([['20240001', '张三', '2']])
  })

  it('keeps an unrecognised first column as a name for validation', () => {
    expect(normalizeVolunteerSheetRows([['张三', '高一一班', '2']])).toEqual([['', '张三', '2']])
  })

  it('supports horizontal workbooks with names on the second row and durations on the third', () => {
    expect(
      normalizeVolunteerSheetRows([
        ['学号', '20240001', '20240002'],
        ['姓名', '张三', '李四'],
        ['时长', '2', '1.5']
      ])
    ).toEqual([
      ['20240001', '张三', '2'],
      ['20240002', '李四', '1.5']
    ])
  })

  it('supports horizontal workbooks with names on the first row and durations on the third', () => {
    expect(
      normalizeVolunteerSheetRows([
        ['姓名', '张三', '李四'],
        ['学号', '20240001', '20240002'],
        ['时长', '2', '1.5']
      ])
    ).toEqual([
      ['20240001', '张三', '2'],
      ['20240002', '李四', '1.5']
    ])
  })

  it('does not mistake a vertical workbook with headers for a horizontal workbook', () => {
    expect(
      normalizeVolunteerSheetRows([
        ['学号', '姓名', '时长'],
        ['20240001', '张三', '2'],
        ['20240002', '李四', '1.5']
      ])
    ).toEqual([
      ['20240001', '张三', '2'],
      ['20240002', '李四', '1.5']
    ])
  })

  it('parses an xlsx workbook through the SheetJS boundary', async () => {
    const workbook = XLSX.utils.book_new()
    const sheet = XLSX.utils.aoa_to_sheet([
      ['姓名', '学号', '时长'],
      ['张三', '20240001', 2]
    ])
    XLSX.utils.book_append_sheet(workbook, sheet, '义工名单')
    const buffer = XLSX.write(workbook, { type: 'array', bookType: 'xlsx' })

    expect(await parseVolunteerXlsx(buffer)).toEqual([['20240001', '张三', '2']])
  })
})
