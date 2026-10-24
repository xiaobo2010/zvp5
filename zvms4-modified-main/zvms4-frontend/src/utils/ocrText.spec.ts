import { describe, expect, it } from 'vitest'
import { parseOcrText } from './ocrText'

describe('OCR 文本拆行', () => {
  it('解析带表头的学号/姓名/班级/时长', () => {
    const rows = parseOcrText(
      ['学号 姓名 班级 时长', '2024001 张三 高一1班 2', '2024002 李四 高一2班 1.5'].join('\n')
    )
    expect(rows).toEqual([
      {
        studentId: '2024001',
        name: '张三',
        duration: 2,
        mode: undefined,
        note: undefined
      },
      {
        studentId: '2024002',
        name: '李四',
        duration: 1.5,
        mode: undefined,
        note: undefined
      }
    ])
  })

  it('识别行号、全角括号班级、小时后缀和模式', () => {
    const [row] = parseOcrText('1 2024003 王五 高一（3）班 1小时 校内')
    expect(row).toMatchObject({
      studentId: '2024003',
      name: '王五',
      duration: 1,
      mode: 'on-campus'
    })
  })

  it('修复被 OCR 插空格的学号', () => {
    const [row] = parseOcrText('2024 004 赵六 2.5 校外')
    expect(row).toMatchObject({
      studentId: '2024004',
      name: '赵六',
      duration: 2.5,
      mode: 'off-campus'
    })
  })

  it('识别标签式字段', () => {
    const [row] = parseOcrText('姓名：周八 学号：2024006 时长：1.5 社会实践')
    expect(row).toMatchObject({
      studentId: '2024006',
      name: '周八',
      duration: 1.5,
      mode: 'social-practice'
    })
  })

  it('姓名行没有学号时保留姓名，交给本地名单补全', () => {
    const rows = parseOcrText('张三\n李四')
    expect(rows).toEqual([
      { studentId: undefined, name: '张三', duration: undefined, mode: undefined, note: undefined },
      { studentId: undefined, name: '李四', duration: undefined, mode: undefined, note: undefined }
    ])
  })

  it('只有学号时保留学号', () => {
    expect(parseOcrText('2024007')).toEqual([
      {
        studentId: '2024007',
        name: undefined,
        duration: undefined,
        mode: undefined,
        note: undefined
      }
    ])
  })

  it('过滤表头、合计、页码和标题', () => {
    expect(
      parseOcrText(['义工登记表', '学号 姓名', '备注 合计', '第1页', '共2页'].join('\n'))
    ).toEqual([])
  })

  it('保留备注并去重完全相同的 OCR 行', () => {
    const rows = parseOcrText('2024008 钱七 高一4班 2 组长\n2024008 钱七 高一4班 2 组长')
    expect(rows).toEqual([
      {
        studentId: '2024008',
        name: '钱七',
        duration: 2,
        mode: undefined,
        note: '组长'
      }
    ])
  })

  it('同名不同学号保留为两行', () => {
    const rows = parseOcrText('2024010 张三 高一1班 2\n2024011 张三 高一2班 1')
    expect(rows.map((row) => row.studentId)).toEqual(['2024010', '2024011'])
  })

  it('空文本返回空数组', () => {
    expect(parseOcrText('')).toEqual([])
    expect(parseOcrText('   \n  ')).toEqual([])
  })
})
