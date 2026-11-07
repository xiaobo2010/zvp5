import { describe, expect, it } from 'vitest'
import { DIFF_CONTEXT, diffLines, diffViewRows, hasChanges } from './textDiff'

describe('textDiff diffLines', () => {
  it('returns nothing for identical text', () => {
    expect(diffLines('same', 'same')).toEqual([])
    expect(diffLines('', '')).toEqual([])
  })

  it('marks pure additions in order', () => {
    const rows = diffLines('a\nc', 'a\nb\nc')
    expect(rows).toEqual([
      { type: 'same', text: 'a' },
      { type: 'add', text: 'b' },
      { type: 'same', text: 'c' }
    ])
  })

  it('marks deletions and groups consecutive deleted lines into one block', () => {
    const rows = diffLines('a\nb\nc', 'a')
    expect(rows).toEqual([
      { type: 'same', text: 'a' },
      { type: 'del', text: 'b\nc' }
    ])
  })

  it('keeps the git convention: the deletion block precedes its addition pair', () => {
    const rows = diffLines('old', 'new')
    expect(rows).toEqual([
      { type: 'del', text: 'old' },
      { type: 'add', text: 'new' }
    ])
  })

  it('survives a full rewrite of staggered lines', () => {
    const rows = diffLines('1\n2\n3\n4', '1\ntwo\nIII\n4')
    // 平局时偏好删除块：删除在前、新增紧随，符合 git 的呈现习惯。
    expect(rows).toEqual([
      { type: 'same', text: '1' },
      { type: 'del', text: '2\n3' },
      { type: 'add', text: 'two\nIII' },
      { type: 'same', text: '4' }
    ])
  })
})

describe('textDiff diffViewRows', () => {
  it('collapses long unchanged middles and keeps the context on both ends', () => {
    const unchanged = Array.from({ length: 12 }, (_, index) => `line${index}`)
    const rows = diffLines(
      [...unchanged, 'changed'].join('\n'),
      [...unchanged, 'replaced'].join('\n')
    )
    const view = diffViewRows(rows)
    expect(view.some((row) => row.type === 'collapsed')).toBe(true)
    expect(view[0]).toEqual({ type: 'same', text: 'line0\nline1' })
    expect(view[1]).toEqual({ type: 'collapsed', count: 8 })
  })

  it('leaves short unchanged runs untouched', () => {
    const rows = diffLines('a\na2\nb', 'a\na2\nc')
    const view = diffViewRows(rows)
    expect(view.every((row) => row.type !== 'collapsed')).toBe(true)
  })

  it('collapses at the configured threshold only', () => {
    const lines = Array.from({ length: 10 }, (_, index) => `x${index}`)
    const text = lines.join('\n')
    const dense = diffViewRows(diffLines(text, `${text}\nextra`), DIFF_CONTEXT)
    expect(dense.some((row) => row.type === 'collapsed')).toBe(true)
    const loose = diffViewRows(diffLines(text, `${text}\nextra`), 10)
    expect(loose.some((row) => row.type === 'collapsed')).toBe(false)
  })
})

describe('textDiff hasChanges', () => {
  it('is a plain inequality guard', () => {
    expect(hasChanges('a', 'a')).toBe(false)
    expect(hasChanges('a', 'a\nb')).toBe(true)
    expect(hasChanges('', '')).toBe(false)
  })
})
