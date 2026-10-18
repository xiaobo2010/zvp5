<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElDialog } from 'element-plus'
import type { AxiosError } from 'axios'
import {
  getSchema,
  getColumns,
  getRows,
  insertRow,
  updateRow,
  deleteRow,
  runQuery,
  runDdl,
  isLob,
  type SchemaItem,
  type ColumnsData,
  type RowsData,
  type QueryData,
  type DdlData,
  type CellValue,
  type LobValue,
  type ColumnDef
} from '@/api/system/dbContent.ts'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'

const { t } = useI18n()

const DEFAULT_LIMIT = 100
const MAX_LIMIT = 200

// ---------- 表清单 ----------
const schemaLoading = ref(false)
const schemaItems = ref<SchemaItem[]>([])
const schemaFilter = ref('')
const selectedTable = ref('')

const filteredSchema = computed(() => {
  const keyword = schemaFilter.value.trim().toUpperCase()
  if (!keyword) return schemaItems.value
  return schemaItems.value.filter((item) => item.name.toUpperCase().includes(keyword))
})

async function loadSchema() {
  schemaLoading.value = true
  try {
    const res = await getSchema()
    schemaItems.value = res.data?.data?.tables ?? []
  } catch {
    schemaItems.value = []
  } finally {
    schemaLoading.value = false
  }
}

function selectTable(name: string) {
  selectedTable.value = name
  orderBy.value = null
  desc.value = false
  page.value = 0
  void Promise.all([loadColumns(), loadRows()])
}

// ---------- 列定义 ----------
const columnsLoading = ref(false)
const columnsData = ref<ColumnsData | null>(null)

const hasPk = computed(() => (columnsData.value?.primary_key.length ?? 0) > 0)

async function loadColumns() {
  columnsLoading.value = true
  columnsData.value = null
  try {
    const res = await getColumns(selectedTable.value)
    columnsData.value = res.data?.data ?? null
  } catch {
    columnsData.value = null
  } finally {
    columnsLoading.value = false
  }
}

// ---------- 数据网格 ----------
const rowsLoading = ref(false)
const rowsData = ref<RowsData | null>(null)
const orderBy = ref<string | null>(null)
const desc = ref(false)
const dataErrorNote = ref('')

async function loadRows() {
  rowsLoading.value = true
  dataErrorNote.value = ''
  try {
    const res = await getRows(
      selectedTable.value,
      page.value * rowsLimit.value,
      rowsLimit.value,
      orderBy.value ?? undefined,
      orderBy.value ? desc.value : undefined
    )
    rowsData.value = res.data?.data ?? null
  } catch (error) {
    rowsData.value = null
    dataErrorNote.value = errorMessageOf(error)
  } finally {
    rowsLoading.value = false
  }
}

const rowsLimit = ref(DEFAULT_LIMIT)
const page = ref(0)

function rowsTotalDisplay(): string {
  const total = rowsData.value?.total_estimate
  if (total === null || total === undefined) {
    // 不做假总数:has_more 只提示「还有更多」
    return t('server.dbContent.totalUnknown')
  }
  return t('server.dbContent.totalEstimate', { count: total })
}

// 分页:total_estimate 可能为 null,用 has_more 推进;页数仅当估算存在时才有意义
function rowsPages(): number {
  const total = rowsData.value?.total_estimate
  if (total === null || total === undefined) {
    return page.value + (rowsData.value?.has_more ? 1 : 0)
  }
  return Math.max(1, Math.ceil(total / rowsLimit.value))
}

function gotoPage(target: number) {
  if (target < 0) return
  if (rowsData.value && !rowsData.value.has_more && target > page.value) return
  if (rowsData.value?.total_estimate !== null && rowsData.value?.total_estimate !== undefined) {
    const max = Math.ceil(rowsData.value.total_estimate / rowsLimit.value) - 1
    if (target > max) return
  }
  page.value = target
  void loadRows()
}

function toggleSort(col: string) {
  if (orderBy.value !== col) {
    orderBy.value = col
    desc.value = false
  } else if (!desc.value) {
    desc.value = true
  } else {
    orderBy.value = null
    desc.value = false
  }
  page.value = 0
  void loadRows()
}

function fmtCell(value: CellValue): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE'
  if (typeof value === 'object') return ''
  return String(value)
}

const lobPreview = ref<LobValue | null>(null)
const lobDialogVisible = ref(false)

const lobPreviewText = computed(() => {
  const lob = lobPreview.value
  if (!lob) return ''
  if (lob.$lob === 'BLOB') return t('server.dbContent.lobNoPreview')
  return lob.preview || t('server.dbContent.queryEmpty')
})

function openLobPreview(value: LobValue) {
  lobPreview.value = value
  lobDialogVisible.value = true
}

function lobBadge(value: LobValue): string {
  const unit = value.$lob === 'BLOB' ? t('server.dbContent.lobBytes') : t('server.dbContent.lobChars')
  return `[${value.$lob} ${value.length} ${unit}]`
}

// ---------- 行编辑(共享表单) ----------
const editing = ref(false)
const insertMode = ref(false)
const editFields = ref<{ name: string; type: string; nullable: boolean; isPk: boolean; text: string }[]>([])
const rowDialogVisible = ref(false)

const numberTypes = new Set(['NUMBER', 'FLOAT', 'BINARY_DOUBLE', 'BINARY_FLOAT', 'INTEGER'])
const timeTypes = new Set(['DATE', 'TIMESTAMP', 'TIMESTAMP(6)', 'DATETIME'])

function fieldMode(type: string): 'number' | 'textarea' | 'text' {
  if (numberTypes.has(type.toUpperCase())) return 'number'
  if (timeTypes.has(type.toUpperCase())) return 'text'
  return 'text'
}

function openInsert() {
  const cols = columnsData.value?.columns ?? []
  editing.value = true
  insertMode.value = true
  editFields.value = cols.map((col) => ({
    name: col.name,
    type: col.type,
    nullable: col.nullable,
    isPk: col.primary_key,
    text: ''
  }))
  rowDialogVisible.value = true
}

function openEdit(row: Record<string, CellValue>) {
  const cols = columnsData.value?.columns ?? []
  editing.value = true
  insertMode.value = false
  editFields.value = cols
    .filter((col) => !isLob(row[col.name]))
    .map((col) => ({
      name: col.name,
      type: col.type,
      nullable: col.nullable,
      isPk: col.primary_key,
      text: row[col.name] === null || row[col.name] === undefined ? '' : String(row[col.name])
    }))
  rowDialogVisible.value = true
}

function rowKey(row: Record<string, CellValue>): Record<string, CellValue> {
  const key: Record<string, CellValue> = {}
  for (const pk of columnsData.value?.primary_key ?? []) {
    key[pk] = row[pk] ?? null
  }
  return key
}

function keyDisplay(key: Record<string, CellValue>): string {
  return Object.entries(key)
    .map(([name, value]) => `${name}=${fmtCell(value)}`)
    .join(', ')
}

function valuesOf(): {
  values: Record<string, CellValue>
  missingPk: string[]
} {
  const values: Record<string, CellValue> = {}
  const missingPk: string[] = []
  for (const field of editFields.value) {
    const trimmed = field.text.trim()
    if (!trimmed) {
      if (insertMode.value && field.isPk) {
        missingPk.push(field.name)
      } else {
        values[field.name] = null
      }
      continue
    }
    if (fieldMode(field.type) === 'number') {
      const num = Number(trimmed)
      values[field.name] = Number.isFinite(num) ? num : trimmed
    } else {
      values[field.name] = trimmed
    }
  }
  return { values, missingPk }
}

// ---------- 双人授权 + 写操作 ----------
const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()
const submitting = ref(false)

async function openAuth(scene: string): Promise<string | null> {
  const dialog = authRef.value
  if (!dialog) return null
  return dialog.open(scene)
}

function errorMessageOf(error: unknown): string {
  const err = error as AxiosError<{ detail?: unknown }>
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  return t('server.auth.failed')
}

async function submitRowChange() {
  const table = selectedTable.value
  const { values, missingPk } = valuesOf()
  if (insertMode.value && missingPk.length > 0) {
    ElMessage({
      message: t('server.dbContent.fieldRequired', { cols: missingPk.join(', ') }),
      type: 'warning',
      grouping: true,
      plain: true
    })
    return
  }
  rowDialogVisible.value = false
  const scene = insertMode.value
    ? t('server.dbContent.insertScene', { table })
    : editingKey === null
      ? ''
      : t('server.dbContent.editScene', { table, key: keyDisplay(editingKey) })
  let code: string | null = null
  if (scene) {
    code = await openAuth(scene)
  }
  if (!code) return
  submitting.value = true
  try {
    if (insertMode.value) {
      await insertRow(table, values, code)
    } else if (editingKey !== null) {
      await updateRow(table, editingKey, values, code)
    }
    authRef.value?.succeed()
    await loadRows()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
    return
  } finally {
    submitting.value = false
  }
}

let editingKey: Record<string, CellValue> | null = null

async function doDelete(row: Record<string, CellValue>) {
  const key = rowKey(row)
  const code = await openAuth(
    t('server.dbContent.deleteScene', { table: selectedTable.value, key: keyDisplay(key) })
  )
  if (!code) return
  submitting.value = true
  try {
    await deleteRow(selectedTable.value, key, code)
    authRef.value?.succeed()
    // 删除后若当页只剩 1 行且不在第一页,回退一页再刷新
    if (page.value > 0 && rowsData.value?.rows.length === 1) {
      page.value -= 1
    }
    await loadRows()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
    return
  } finally {
    submitting.value = false
  }
}

// ---------- SQL 查询(只读) ----------
const sqlText = ref('')
const sqlLimit = ref(100)
const sqlRunning = ref(false)
const queryResult = ref<QueryData | null>(null)
const queryError = ref('')

async function executeQuery() {
  const sql = sqlText.value.trim()
  if (!sql || !/^(SELECT|WITH)\b/i.test(sql)) {
    ElMessage({
      message: t('server.dbContent.queryMustSelect'),
      type: 'warning',
      grouping: true,
      plain: true
    })
    return
  }
  if (sql.includes(';')) {
    ElMessage({
      message: t('server.dbContent.querySingleStatement'),
      type: 'warning',
      grouping: true,
      plain: true
    })
    return
  }
  sqlRunning.value = true
  queryError.value = ''
  try {
    const res = await runQuery(sql, sqlLimit.value)
    queryResult.value = res.data?.data ?? null
  } catch (error) {
    queryResult.value = null
    queryError.value = errorMessageOf(error)
  } finally {
    sqlRunning.value = false
  }
}

// ---------- DDL / DML ----------
const ddlText = ref('')
const ddlConfirm = ref('')
const ddlNote = ref('')
const ddlRunning = ref(false)
const ddlResult = ref<DdlData | null>(null)
const ddlError = ref('')

function isDestructive(sql: string): boolean {
  const upper = sql.toUpperCase().trim()
  if (/^(DROP|TRUNCATE)\b/.test(upper)) return true
  if (/^(DELETE|UPDATE)\b/.test(upper) && !/\bWHERE\b/.test(upper)) return true
  return false
}

async function executeDdl() {
  const text = ddlText.value.trim()
  const body = text.replace(/;+\s*$/, '')
  if (!body || text.replace(/;$/, '').includes(';')) {
    ElMessage({
      message: t('server.dbContent.ddlSingleStatement'),
      type: 'warning',
      grouping: true,
      plain: true
    })
    return
  }
  if (isDestructive(body)) {
    const target = ddlConfirm.value.trim().toUpperCase()
    if (!target) {
      ElMessage({
        message: t('server.dbContent.ddlConfirmRequired'),
        type: 'warning',
        grouping: true,
        plain: true
      })
      return
    }
    // 从语句里抽取目标表名,与 confirm 粗校验;严格校验由后端 400 兜底
    const match = body.match(
      /^(?:DROP TABLE|TRUNCATE TABLE|DELETE FROM|UPDATE)\s+([A-Za-z][A-Za-z0-9_$#]*)/i
    )
    if (match && match[1].toUpperCase() !== target) {
      ElMessage({
        message: t('server.dbContent.ddlConfirmMismatch', { table: match[1].toUpperCase() }),
        type: 'warning',
        grouping: true,
        plain: true
      })
      return
    }
  }
  const code = await openAuth(t('server.dbContent.ddlScene'))
  if (!code) return
  ddlRunning.value = true
  ddlError.value = ''
  try {
    const res = await runDdl(body, ddlConfirm.value.trim(), code)
    ddlResult.value = res.data?.data ?? null
    ddlNote.value = t('server.dbContent.ddlCommitted')
  } catch (error) {
    ddlError.value = errorMessageOf(error)
  } finally {
    ddlRunning.value = false
  }
}

void loadSchema()
</script>

<template>
  <div class="flex flex-col gap-3 pb-2">
    <!-- 页面标题行 -->
    <div class="flex flex-wrap items-end justify-between gap-2 px-1">
      <div class="flex flex-col gap-0.5">
        <h2 class="m-0 text-[22px] font-semibold leading-7 text-[var(--zv-text)]">{{ t('server.tabs.dbContent') }}</h2>
        <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.dbContent.selTableHint') }}</span>
      </div>
    </div>

    <div class="flex h-[calc(100vh-330px)] min-h-[560px] gap-3">
      <!-- 左:表清单 -->
      <aside class="flex w-64 shrink-0 flex-col border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-4">
        <div class="d-label mb-2">{{ t('server.dbContent.schemaCount', { count: filteredSchema.length }) }}</div>
        <input
          v-model="schemaFilter"
          :placeholder="t('server.dbContent.searchPlaceholder')"
          class="mb-3 w-full border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-3 text-[14px] text-[var(--zv-text)] outline-none focus:border-[var(--zv-hairline-strong)]"
          style="height: 34px"
        />
        <div class="min-h-0 flex-1 overflow-auto">
          <button
            v-for="item in filteredSchema"
            :key="item.name"
            class="block w-full border-0 px-1 py-2 text-left transition-colors"
            :class="selectedTable === item.name ? 'bg-[var(--zv-surface-alt)]' : 'bg-transparent hover:bg-[var(--zv-surface-alt)]'"
            @click="selectTable(item.name)"
          >
            <span class="block truncate text-[14px] font-medium text-[var(--zv-text)]">{{ item.name }}</span>
            <span class="block text-[12px] tabular-nums text-[var(--zv-label)]">
              {{ item.type }}
              · {{ item.num_rows === null ? '—' : t('server.dbContent.totalEstimate', { count: item.num_rows }) }}
            </span>
          </button>
          <p v-if="!schemaLoading && filteredSchema.length === 0" class="px-1 py-4 text-[13px] text-[var(--zv-label)]">
            {{ t('server.dbContent.queryEmpty') }}
          </p>
          <p v-if="schemaLoading" class="px-1 py-4 text-[13px] text-[var(--zv-label)]">…</p>
        </div>
      </aside>

      <!-- 右区 -->
      <div class="flex min-w-0 flex-1 flex-col gap-2">
        <div class="grid shrink-0 grid-cols-1 items-stretch gap-3 xl:grid-cols-2">
          <!-- 列定义 -->
          <section class="border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-4">
            <h3 class="m-0 mb-2 text-[16px] font-medium text-[var(--zv-text)]">{{ t('server.dbContent.columnsTitle') }}</h3>
            <div class="max-h-56 overflow-auto" v-if="columnsData">
              <table class="d-table w-full border-collapse text-[14px] tabular-nums">
                <thead class="sticky top-0 bg-[var(--zv-surface-alt)]">
                  <tr class="border-b border-[var(--zv-hairline-strong)]">
                    <th class="d-label px-2 py-1.5 text-left font-normal">{{ t('server.dbContent.colCol') }}</th>
                    <th class="d-label px-2 py-1.5 text-left font-normal">{{ t('server.dbContent.colColType') }}</th>
                    <th class="d-label px-2 py-1.5 text-right font-normal">{{ t('server.dbContent.colLen') }}</th>
                    <th class="d-label px-2 py-1.5 text-center font-normal">{{ t('server.dbContent.colNullable') }}</th>
                    <th class="d-label px-2 py-1.5 text-center font-normal">{{ t('server.dbContent.colPk') }}</th>
                    <th class="d-label px-2 py-1.5 text-left font-normal">{{ t('server.dbContent.colDefault') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="col in columnsData.columns"
                    :key="col.name"
                    class="border-b border-[var(--zv-hairline)]"
                  >
                    <td class="px-2 py-1.5 text-[var(--zv-text)]">{{ col.name }}</td>
                    <td class="px-2 py-1.5 text-[var(--zv-text-sub)]">{{ col.type }}</td>
                    <td class="px-2 py-1.5 text-right text-[var(--zv-text-sub)]">{{ col.length ?? '—' }}</td>
                    <td class="px-2 py-1.5 text-center text-[var(--zv-text-sub)]">
                      {{ col.nullable ? t('server.dbContent.nullableYes') : '—' }}
                    </td>
                    <td class="px-2 py-1.5 text-center text-[var(--zv-text-sub)]">{{ col.primary_key ? 'PK' : '—' }}</td>
                    <td class="px-2 py-1.5 text-[var(--zv-text-sub)]">{{ fmtCell(col.default as CellValue) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="py-8 text-center text-[13px] text-[var(--zv-label)]">
              {{ columnsLoading ? '…' : t('server.dbContent.selTableHint') }}
            </div>
          </section>

          <!-- SQL 查询 + DDL -->
          <div class="flex flex-col gap-3">
            <section class="border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-4">
              <h3 class="m-0 mb-2 text-[16px] font-medium text-[var(--zv-text)]">{{ t('server.dbContent.sqlTitle') }}</h3>
              <textarea
                v-model="sqlText"
                rows="3"
                :placeholder="t('server.dbContent.sqlPlaceholder')"
                class="sql-area w-full resize-none border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] p-2 text-[13px] leading-5 text-[var(--zv-text)] outline-none focus:border-[var(--zv-hairline-strong)]"
              ></textarea>
              <div class="mt-2 flex items-center gap-3">
                <button
                  class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-text)] px-4 text-[14px] text-[var(--zv-surface)] transition-colors hover:opacity-80 disabled:opacity-50"
                  style="height: 34px"
                  :disabled="sqlRunning"
                  @click="executeQuery"
                >
                  {{ t('server.dbContent.sqlRun') }}
                </button>
                <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.dbContent.sqlLimitLabel') }}</span>
                <input
                  v-model.number="sqlLimit"
                  type="number"
                  min="1"
                  max="500"
                  class="w-16 border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-2 py-0.5 text-right text-[13px] tabular-nums text-[var(--zv-text)] outline-none"
                  style="height: 34px"
                />
                <span v-if="queryError" class="text-[12px] text-[#b42318] dark:text-[#ff8a7a]">{{ queryError }}</span>
              </div>
              <div v-if="queryResult" class="mt-3">
                <div class="mb-1 flex items-center gap-4 text-[12px] text-[var(--zv-label)]">
                  <span>{{ t('server.dbContent.queryElapsed', { ms: queryResult.elapsed_ms }) }}</span>
                  <span v-if="queryResult.truncated">{{ t('server.dbContent.queryTruncated', { limit: sqlLimit }) }}</span>
                </div>
                <div class="max-h-40 overflow-auto border border-[var(--zv-hairline)]">
                  <table class="d-table w-full border-collapse text-[13px] tabular-nums">
                    <thead class="sticky top-0 bg-[var(--zv-surface-alt)]">
                      <tr class="border-b border-[var(--zv-hairline-strong)]">
                        <th
                          v-for="col in queryResult.columns"
                          :key="col"
                          class="d-label px-2 py-1.5 text-left font-normal"
                        >
                          {{ col }}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, index) in queryResult.rows" :key="index" class="border-b border-[var(--zv-hairline)]">
                        <td
                          v-for="col in queryResult.columns"
                          :key="col"
                          class="max-w-48 truncate px-2 py-1.5 text-[var(--zv-text-sub)]"
                        >
                          <template v-if="isLob(row[col])">
                            <button class="lob-badge underline" @click="openLobPreview(row[col] as LobValue)">
                              {{ lobBadge(row[col] as LobValue) }}
                            </button>
                          </template>
                          <template v-else>{{ fmtCell(row[col]) }}</template>
                        </td>
                      </tr>
                      <tr v-if="queryResult.rows.length === 0">
                        <td :colspan="queryResult.columns.length" class="px-2 py-2 text-center text-[12px] text-[var(--zv-label)]">
                          {{ t('server.dbContent.queryEmpty') }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            <section class="border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-4">
              <h3 class="m-0 mb-2 text-[16px] font-medium text-[var(--zv-text)]">{{ t('server.dbContent.ddlTitle') }}</h3>
              <textarea
                v-model="ddlText"
                rows="3"
                :placeholder="t('server.dbContent.ddlPlaceholder')"
                class="sql-area w-full resize-none border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] p-2 text-[13px] leading-5 text-[var(--zv-text)] outline-none focus:border-[var(--zv-hairline-strong)]"
              ></textarea>
              <div class="mt-2 flex flex-wrap items-center gap-3">
                <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.dbContent.ddlConfirmLabel') }}</span>
                <input
                  v-model="ddlConfirm"
                  :placeholder="t('server.dbContent.ddlConfirmPlaceholder')"
                  class="w-32 border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-2 text-[13px] uppercase text-[var(--zv-text)] outline-none"
                  style="height: 34px"
                />
                <button
                  class="border border-[#b42318] bg-[#b42318] px-4 text-[14px] text-white transition-colors hover:opacity-80 disabled:opacity-50"
                  style="height: 34px"
                  :disabled="ddlRunning"
                  @click="executeDdl"
                >
                  {{ t('server.dbContent.ddlRun') }}
                </button>
              </div>
              <p class="mt-2 text-[12px] leading-4 text-[var(--zv-label)]">{{ t('server.dbContent.ddlCommitted') }}</p>
              <div v-if="ddlError" class="mt-1 text-[13px] leading-5 text-[#b42318] dark:text-[#ff8a7a]">{{ ddlError }}</div>
              <div v-else-if="ddlResult" class="mt-1 text-[13px] leading-5 text-[var(--zv-text-sub)]">
                {{ t('server.dbContent.ddlRowcount') }}: {{ ddlResult.rowcount === null ? '—' : ddlResult.rowcount }}
                · {{ t('server.dbContent.ddlSummary') }}: {{ ddlResult.summary }}
              </div>
              <div v-else class="mt-1 text-[13px] leading-5 text-[var(--zv-label)]">{{ t('server.dbContent.ddlNone') }}</div>
            </section>
          </div>
        </div>

        <!-- 数据网格 -->
        <section class="flex min-h-0 flex-1 flex-col border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-4">
          <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-3">
              <h3 class="m-0 text-[16px] font-medium text-[var(--zv-text)]">{{ t('server.dbContent.gridTitle') }}</h3>
              <span v-if="selectedTable" class="text-[14px] font-medium text-[var(--zv-text)] font-mono">{{ selectedTable }}</span>
              <span v-if="selectedTable && !hasPk" class="text-[12px] text-[#b42318] dark:text-[#ff8a7a]">
                {{ t('server.dbContent.noPkNote') }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <button
                class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] enabled:hover:bg-[var(--zv-surface-alt)] disabled:opacity-40"
                style="height: 34px"
                :disabled="!selectedTable"
                @click="openInsert"
              >
                {{ t('server.dbContent.insert') }}
              </button>
              <span class="text-[12px] tabular-nums text-[var(--zv-label)]">
                {{ rowsData ? t('server.dbContent.totalLabel') + ' ' + rowsTotalDisplay() : '' }}
              </span>
              <button
                class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[13px] tabular-nums text-[var(--zv-text)] disabled:opacity-40 hover:bg-[var(--zv-surface-alt)]"
                style="height: 34px"
                :disabled="page <= 0 || rowsLoading"
                @click="gotoPage(page - 1)"
              >
                ‹ {{ t('server.dbContent.pagePrev') }}
              </button>
              <span class="text-[12px] tabular-nums text-[var(--zv-label)]">
                {{ rowsData ? t('server.dbContent.pageInfo', { page: page + 1, pages: Math.max(1, rowsPages()) }) : '' }}
              </span>
              <button
                class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[13px] tabular-nums text-[var(--zv-text)] disabled:opacity-40 hover:bg-[var(--zv-surface-alt)]"
                style="height: 34px"
                :disabled="rowsLoading || !rowsData || !rowsData.has_more"
                @click="gotoPage(page + 1)"
              >
                {{ t('server.dbContent.pageNext') }} ›
              </button>
            </div>
          </div>

          <div v-if="dataErrorNote" class="mb-2 text-[13px] text-[#b42318] dark:text-[#ff8a7a]">{{ dataErrorNote }}</div>

          <div v-if="rowsData" class="min-h-0 flex-1 overflow-auto">
            <table class="d-table w-full border-collapse text-[14px] tabular-nums">
              <thead class="sticky top-0 bg-[var(--zv-surface-alt)]">
                <tr class="border-b border-[var(--zv-hairline-strong)]">
                  <th
                    v-for="col in columnsData?.columns ?? []"
                    :key="col.name"
                    class="d-label whitespace-nowrap px-3 py-2 text-left font-normal hover:text-[var(--zv-text)] cursor-pointer"
                    @click="toggleSort(col.name)"
                  >
                    {{ col.name }}
                    <template v-if="orderBy === col.name">{{ desc ? '▼' : '▲' }}</template>
                  </th>
                  <th v-if="hasPk" class="d-label px-3 py-2 text-right font-normal">
                    {{ t('server.dbContent.rowActions') }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, index) in rowsData.rows"
                  :key="index"
                  class="border-b border-[var(--zv-hairline)] hover:bg-[var(--zv-surface-alt)]"
                >
                  <td
                    v-for="col in columnsData?.columns ?? []"
                    :key="col.name"
                    class="max-w-56 whitespace-nowrap px-3 py-2 text-[var(--zv-text-sub)]"
                  >
                    <template v-if="isLob(row[col.name] as CellValue)">
                      <button
                        class="lob-badge"
                        @click="openLobPreview(row[col.name] as LobValue)"
                      >
                        {{ lobBadge(row[col.name] as LobValue) }}
                      </button>
                    </template>
                    <template v-else>
                      <span class="block max-w-56 truncate">{{ fmtCell(row[col.name] as CellValue) }}</span>
                    </template>
                  </td>
                  <td v-if="hasPk" class="whitespace-nowrap px-3 py-1.5 text-right">
                    <button class="d-link" @click="openEdit(row)">
                      {{ t('server.dbContent.edit') }}
                    </button>
                    <button class="d-link-danger" @click="doDelete(row)">
                      {{ t('server.dbContent.del') }}
                    </button>
                  </td>
                </tr>
                <tr v-if="rowsData.rows.length === 0">
                  <td :colspan="columnsData?.columns.length ?? 1" class="px-3 py-6 text-center text-[13px] text-[var(--zv-label)]">
                    —
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="flex flex-1 items-center justify-center text-[13px] text-[var(--zv-label)]">
            {{ rowsLoading ? '…' : t('server.dbContent.selTableHint') }}
          </div>
        </section>
      </div>
    </div>

    <!-- 插入 / 编辑表单 -->
    <ElDialog
      v-model="rowDialogVisible"
      :title="insertMode ? t('server.dbContent.insertDialogTitle', { table: selectedTable }) : t('server.dbContent.editDialogTitle', { table: selectedTable })"
      width="440px"
      append-to-body
    >
      <div class="max-h-[60vh] overflow-auto pr-1">
        <div v-for="field in editFields" :key="field.name" class="mb-3">
          <div class="d-label mb-1">
            {{ field.name }}
            <span class="normal-case">· {{ field.type }}{{ field.isPk ? ' · PK' : field.nullable ? '' : ' · NOT NULL' }}</span>
          </div>
          <input
            v-model="field.text"
            class="w-full border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-3 text-[14px] text-[var(--zv-text)] outline-none focus:border-[var(--zv-hairline-strong)]"
            style="height: 34px"
            :placeholder="t('server.dbContent.nullHint')"
          />
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-4 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            style="height: 34px"
            @click="rowDialogVisible = false"
          >
            {{ t('server.auth.cancel') }}
          </button>
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-text)] px-4 text-[14px] text-[var(--zv-surface)] hover:opacity-80"
            style="height: 34px"
            @click="submitRowChange"
          >
            {{ t('server.dbContent.proceedAuth') }}
          </button>
        </div>
      </template>
    </ElDialog>

    <!-- LOB 预览 -->
    <ElDialog
      v-model="lobDialogVisible"
      :title="lobPreview ? `${lobPreview.$lob} · ${lobPreview.length}` : ''"
      width="600px"
      append-to-body
    >
      <pre class="max-h-[50vh] overflow-auto whitespace-pre-wrap break-all border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] p-3 text-[13px] leading-5 text-[var(--zv-text)]">{{ lobPreviewText }}</pre>
      <p class="mt-2 text-[12px] text-[var(--zv-label)]">{{ t('server.dbContent.lobTitle') }}</p>
    </ElDialog>

    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
/* 本页局部 token:建议后续由 flat.css 统一收口(另一车道) */
.d-label {
  font-size: 12px;
  line-height: 1.35;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--zv-label);
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}

.sql-area {
  font-family: ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace;
}

.lob-badge {
  display: inline-block;
  border: 1px solid var(--zv-hairline);
  background: var(--zv-surface-alt);
  border-radius: 2px;
  padding: 1px 6px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--zv-text);
  cursor: pointer;
}

.lob-badge:hover {
  border-color: var(--zv-hairline-strong);
}

.d-link,
.d-link-danger {
  border: 0;
  background: transparent;
  padding: 0 6px;
  font-size: 14px;
  height: 28px;
  cursor: pointer;
  text-decoration: underline;
}

.d-link {
  color: var(--zv-text-sub);
}

.d-link:hover {
  color: var(--zv-text);
}

.d-link-danger {
  color: #b42318;
}

.dark .d-link-danger,
:global(html.dark) .d-link-danger {
  color: #ff8a7a;
}
</style>
