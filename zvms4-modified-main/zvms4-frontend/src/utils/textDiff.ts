export type DiffRowType = 'same' | 'add' | 'del'

export type DiffRow = {
  type: DiffRowType
  text: string
}

/**
 * 行级 diff（LCS）。输出顺序与 git 习惯一致：删除块在前、新增块紧随其后。
 * 无新依赖，供历史对比视图与单测共用。
 */
export function diffLines(oldText: string, newText: string): DiffRow[] {
  if (oldText === newText) {
    return []
  }
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const width = b.length + 1
  const table = new Uint32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + (j + 1)] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + (j + 1)])
    }
  }
  const rows: DiffRow[] = []
  const push = (type: DiffRowType, text: string) => {
    const previous = rows[rows.length - 1]
    if (previous && previous.type === type) {
      previous.text += `\n${text}`
      return
    }
    rows.push({ type, text })
  }
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push('same', a[i])
      i++
      j++
    } else if (table[(i + 1) * width + j] >= table[i * width + (j + 1)]) {
      push('del', a[i])
      i++
    } else {
      push('add', b[j])
      j++
    }
  }
  while (i < a.length) {
    push('del', a[i])
    i++
  }
  while (j < b.length) {
    push('add', b[j])
    j++
  }
  return rows
}

export const DIFF_CONTEXT = 2

export type CollapsedRow = {
  type: 'collapsed'
  count: number
}

export type DiffViewRow = DiffRow | CollapsedRow

/**
 * 渲染视图：连续无变化行超过 2*DIFF_CONTEXT 时，首尾各保留 DIFF_CONTEXT 行，
 * 中间折叠为一个 collapsed 占位行，突出真正发生变化的块。
 */
export function diffViewRows(rows: DiffRow[], context: number = DIFF_CONTEXT): DiffViewRow[] {
  const view: DiffViewRow[] = []
  let sameBuffer: string[] = []
  const flushSame = () => {
    if (sameBuffer.length === 0) {
      return
    }
    if (sameBuffer.length > context * 2) {
      view.push({ type: 'same', text: sameBuffer.slice(0, context).join('\n') })
      view.push({ type: 'collapsed', count: sameBuffer.length - context * 2 })
      view.push({ type: 'same', text: sameBuffer.slice(-context).join('\n') })
    } else {
      view.push({ type: 'same', text: sameBuffer.join('\n') })
    }
    sameBuffer = []
  }
  for (const row of rows) {
    if (row.type === 'same') {
      sameBuffer.push(...row.text.split('\n'))
      continue
    }
    flushSame()
    const last = view[view.length - 1]
    if (last && last.type === row.type && 'text' in last) {
      last.text = `${last.text}\n${row.text}`
    } else {
      view.push({ ...row })
    }
  }
  flushSame()
  return view
}

export function hasChanges(oldText: string, newText: string): boolean {
  return oldText !== newText
}
