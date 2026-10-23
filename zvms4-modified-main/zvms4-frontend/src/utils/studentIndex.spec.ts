import { describe, expect, it, vi } from 'vitest'

// 纯函数测试不需要真实的 axios/路由/element-plus 依赖链。
vi.mock('@/plugins/axios', () => ({ default: vi.fn() }))

import {
  lookupStudent,
  matchStudentRow,
  normalizePersonName,
  normalizeStudentId,
  suggestStudents,
  type StudentRecord
} from './studentIndex'

const students: StudentRecord[] = [
  { oid: 'o1', studentId: '2024001', name: '张三', classId: 'c1', className: '高一(1)班' },
  { oid: 'o2', studentId: '2024002', name: '李四', classId: 'c1', className: '高一(1)班' },
  { oid: 'o3', studentId: '2024011', name: '张伟', classId: 'c1', className: '高一(1)班' },
  // 同名两人：任何匹配都不能替用户挑其中一个。
  { oid: 'o4', studentId: '2024005', name: '王五', classId: 'c2', className: '高一(2)班' },
  { oid: 'o5', studentId: '2024015', name: '王五', classId: 'c2', className: '高一(2)班' },
  { oid: 'o6', studentId: '2024100', name: '张三丰', classId: 'c3', className: '高一(3)班' }
]

describe('normalizeStudentId', () => {
  it('converts full-width digits to ASCII', () => {
    expect(normalizeStudentId('２０２４００１')).toBe('2024001')
  })

  it('removes leading, trailing and internal whitespace', () => {
    expect(normalizeStudentId(' 2024 001\t')).toBe('2024001')
  })

  it('keeps unexpected characters instead of silently rewriting the id', () => {
    expect(normalizeStudentId(' 2024-001 ')).toBe('2024-001')
  })

  it('returns an empty string for blank input', () => {
    expect(normalizeStudentId('')).toBe('')
    expect(normalizeStudentId('   ')).toBe('')
  })
})

describe('normalizePersonName', () => {
  it('collapses whitespace including the ideographic space', () => {
    expect(normalizePersonName(' 张\u3000  三 ')).toBe('张 三')
  })

  it('strips zero-width characters without altering Chinese characters', () => {
    expect(normalizePersonName('张\u200b三\ufeff')).toBe('张三')
  })

  it('returns an empty string for blank input', () => {
    expect(normalizePersonName(' \u3000\t')).toBe('')
  })
})

describe('lookupStudent', () => {
  it('finds an exact student id and reports no candidates', () => {
    expect(lookupStudent(students, ' ２０２４００１ ', 'id')).toEqual({
      exact: students[0],
      candidates: []
    })
  })

  it('lists prefix candidates before contains matches for id queries', () => {
    const { exact, candidates } = lookupStudent(students, '202400', 'id')
    expect(exact).toBeNull()
    expect(candidates.map((item) => item.studentId)).toEqual(['2024001', '2024002', '2024005'])
  })

  it('finds an exact name', () => {
    expect(lookupStudent(students, '李四', 'name')).toEqual({
      exact: students[1],
      candidates: []
    })
  })

  it('does not pick one of several same-name students', () => {
    const { exact, candidates } = lookupStudent(students, '王五', 'name')
    expect(exact).toBeNull()
    expect(candidates.map((item) => item.oid)).toEqual(['o4', 'o5'])
  })

  it('returns prefix then contains candidates for name queries', () => {
    const { exact, candidates } = lookupStudent(students, '张', 'name')
    expect(exact).toBeNull()
    expect(candidates.map((item) => item.name)).toEqual(['张三', '张伟', '张三丰'])
  })

  it('ignores an empty query', () => {
    expect(lookupStudent(students, '   ', 'id')).toEqual({ exact: null, candidates: [] })
    expect(lookupStudent(students, '   ', 'name')).toEqual({ exact: null, candidates: [] })
  })
})

describe('suggestStudents', () => {
  it('ranks id prefix matches and honours the limit', () => {
    expect(suggestStudents(students, '202400', 'id', 2).map((item) => item.studentId)).toEqual([
      '2024001',
      '2024002'
    ])
  })

  it('keeps every same-name student as a candidate', () => {
    expect(suggestStudents(students, '王五', 'name').map((item) => item.oid)).toEqual(['o4', 'o5'])
  })

  it('returns nothing for an empty query or a zero limit', () => {
    expect(suggestStudents(students, '', 'id')).toEqual([])
    expect(suggestStudents(students, '2024', 'id', 0)).toEqual([])
  })
})

describe('matchStudentRow', () => {
  it('reports an empty row when both cells are blank', () => {
    expect(matchStudentRow(students, '  ', '')).toMatchObject({
      status: 'empty',
      record: null,
      candidates: []
    })
  })

  it('matches by id alone', () => {
    const match = matchStudentRow(students, '2024001', '')
    expect(match.status).toBe('ok')
    expect(match.record).toBe(students[0])
    expect(match.message).toBe('已由学号匹配')
  })

  it('reports a missing id', () => {
    const match = matchStudentRow(students, '2099999', '')
    expect(match.status).toBe('missing')
    expect(match.record).toBeNull()
    expect(match.message).toBe('本地名单未找到该学号')
  })

  it('matches by a unique name', () => {
    const match = matchStudentRow(students, '', '李四')
    expect(match.status).toBe('ok')
    expect(match.record).toBe(students[1])
    expect(match.message).toBe('已由姓名匹配')
  })

  it('asks the user to choose between same-name students', () => {
    const match = matchStudentRow(students, '', '王五')
    expect(match.status).toBe('ambiguous')
    expect(match.record).toBeNull()
    expect(match.candidates.map((item) => item.oid)).toEqual(['o4', 'o5'])
    expect(match.message).toBe('存在同名同学，请选择')
  })

  it('reports a missing name', () => {
    expect(matchStudentRow(students, '', '不存在').status).toBe('missing')
  })

  it('accepts id and name that agree after normalization', () => {
    const match = matchStudentRow(students, ' ２０２４００１ ', '张 三')
    expect(match.status).toBe('ok')
    expect(match.record).toBe(students[0])
  })

  it('flags a mismatch, keeping the id record as the trusted one', () => {
    const match = matchStudentRow(students, '2024001', '李四')
    expect(match.status).toBe('mismatch')
    expect(match.record).toBe(students[0])
    expect(match.candidates.map((item) => item.oid)).toEqual(['o1', 'o2'])
    expect(match.message).toBe('学号与姓名不一致')
  })

  it('falls back to a unique name when the id is unknown', () => {
    const match = matchStudentRow(students, '2099999', '李四')
    expect(match.status).toBe('mismatch')
    expect(match.record).toBe(students[1])
    expect(match.message).toBe('学号与姓名不一致')
  })

  it('stays ambiguous when the id is unknown but the name is shared', () => {
    const match = matchStudentRow(students, '2099999', '王五')
    expect(match.status).toBe('ambiguous')
    expect(match.record).toBeNull()
    expect(match.candidates.map((item) => item.oid)).toEqual(['o4', 'o5'])
  })

  it('reports a missing row when neither id nor name exists locally', () => {
    const match = matchStudentRow(students, '2099999', '不存在')
    expect(match.status).toBe('missing')
    expect(match.record).toBeNull()
    expect(match.candidates).toEqual([])
  })

  it('never silently resolves an ambiguous name to a single record', () => {
    const match = matchStudentRow(students, '', '王五')
    expect(match.record).toBeNull()
    expect(match.candidates).toHaveLength(2)
    expect(new Set(match.candidates.map((item) => item.oid)).size).toBe(2)
  })
})
