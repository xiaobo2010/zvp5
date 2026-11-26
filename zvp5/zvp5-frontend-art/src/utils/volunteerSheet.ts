export type VolunteerSheetRow = [string, string, string]

const HEADER_WORDS = new Set(['学号', '编号', '姓名', '时长', '小时', 'duration', 'name', 'id'])

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

function isNumericStart(value: string): boolean {
  return /^\d/.test(value.trim())
}

function isHeader(row: string[]): boolean {
  return row.some((cell) => HEADER_WORDS.has(cell.toLowerCase().replace(/\s+/g, '')))
}

function numericStartRatio(row: string[]): number {
  const values = row.filter(Boolean)
  return values.length ? values.filter(isNumericStart).length / values.length : 0
}

/**
 * 常见义工表格是三列：姓名/学号/时长或学号/姓名/时长。
 * 学号列以数字开头即可判定方向，第三列始终按时长处理；其它列由页面继续校验。
 */
export function normalizeVolunteerSheetRows(matrix: unknown[][]): VolunteerSheetRow[] {
  const rows = matrix.map((row) => row.map(text))

  // 常见横向工作簿：前两行分别是学号/姓名（顺序可互换），第三行是对应时长。
  // 至少两列数据才启用此布局，避免把普通的「每行一人」记录误判成横向表格。
  const [first = [], second = [], durations = []] = rows
  const populatedColumns = Math.max(first.length, second.length, durations.length)
  const firstTwoHaveData = first.filter(Boolean).length >= 2 && second.filter(Boolean).length >= 2
  const durationCells = durations.filter((value) => /^\d/.test(value)).length
  const firstHeaderCount = first.filter((value) =>
    HEADER_WORDS.has(value.toLowerCase().replace(/\s+/g, ''))
  ).length
  const secondHeaderCount = second.filter((value) =>
    HEADER_WORDS.has(value.toLowerCase().replace(/\s+/g, ''))
  ).length
  const firstLooksLikeIds = numericStartRatio(first) >= 0.5
  const secondLooksLikeIds = numericStartRatio(second) >= 0.5
  const firstLooksLikeNames = numericStartRatio(first) <= 0.25
  const secondLooksLikeNames = numericStartRatio(second) <= 0.25
  const horizontalLayout =
    (firstLooksLikeIds && secondLooksLikeNames) || (secondLooksLikeIds && firstLooksLikeNames)
  if (
    populatedColumns >= 3 &&
    firstTwoHaveData &&
    durationCells >= 2 &&
    horizontalLayout &&
    firstHeaderCount < 2 &&
    secondHeaderCount < 2
  ) {
    const nameRow = firstLooksLikeIds && !secondLooksLikeIds ? second : first
    const idRow = nameRow === first ? second : first
    const result: VolunteerSheetRow[] = []
    for (let column = 0; column < populatedColumns; column += 1) {
      const rawName = nameRow[column] ?? ''
      const rawId = idRow[column] ?? ''
      if (HEADER_WORDS.has(rawName.toLowerCase().replace(/\s+/g, ''))) continue
      if (!rawName && !rawId && !durations[column]) continue
      const id = isNumericStart(rawId) ? rawId : ''
      const name = isNumericStart(rawName) && !id ? '' : rawName
      result.push([id, name, durations[column] ?? ''])
    }
    return result
  }

  const populatedRows = rows.filter((row) => row.some(Boolean))
  const data =
    populatedRows.length && isHeader(populatedRows[0]) ? populatedRows.slice(1) : populatedRows

  return data.map((row) => {
    const first = row[0] ?? ''
    const second = row[1] ?? ''
    const duration = row[2] ?? ''
    if (isNumericStart(first) && !isNumericStart(second)) return [first, second, duration]
    if (!isNumericStart(first) && isNumericStart(second)) return [second, first, duration]
    // 无法确认学号时保留姓名，交给现有学生匹配和校验逻辑处理。
    return ['', first || second, duration]
  })
}

export async function parseVolunteerXlsx(buffer: ArrayBuffer): Promise<VolunteerSheetRow[]> {
  // xlsx 约 400KB，只在真正导入表格时才需要；放进函数体内动态加载，
  // 避免登记页首包为一次可能用不到的导入付体积。
  const XLSX = await import('xlsx')
  const workbook = XLSX.read(buffer, { type: 'array', cellDates: false })
  const sheet = workbook.Sheets[workbook.SheetNames[0] ?? '']
  if (!sheet) return []
  const matrix = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
    header: 1,
    raw: false,
    defval: ''
  })
  return normalizeVolunteerSheetRows(matrix)
}

export function volunteerRowsToTsv(rows: VolunteerSheetRow[]): string {
  return rows.map((row) => row.join('\t')).join('\n')
}
