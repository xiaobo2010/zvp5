<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElDialog } from 'element-plus/es/components/dialog/index.mjs'
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
import { resolveErrorMessage } from '@/utils/errorMessage'
import { Plus } from '@element-plus/icons-vue'
import ZHexUnit from './components/ZHexUnit.vue'

const { t } = useI18n()

const DEFAULT_LIMIT = 100
const MAX_LIMIT = 200

// ---------- 表清单 ----------
const schemaLoading = ref(false)
const schemaItems = ref<SchemaItem[]>([])
// 输入框的值立刻回显（打字手感），过滤用的关键字去抖 180ms：
// 表名多的时候每敲一个字母重算一次列表，肉眼可见地卡。
const schemaFilterInput = ref('')
const schemaFilter = ref('')
const selectedTable = ref('')

const filteredSchema = computed(() => {
  const keyword = schemaFilter.value.trim().toUpperCase()
  if (!keyword) return schemaItems.value
  return schemaItems.value.filter((item) => item.name.toUpperCase().includes(keyword))
})

let filterTimer: ReturnType<typeof setTimeout> | undefined

watch(schemaFilterInput, (value) => {
  if (filterTimer !== undefined) {
    clearTimeout(filterTimer)
  }
  filterTimer = setTimeout(() => {
    filterTimer = undefined
    schemaFilter.value = value
  }, 180)
})

onUnmounted(() => {
  if (filterTimer !== undefined) {
    clearTimeout(filterTimer)
  }
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
    dataErrorNote.value = resolveErrorMessage(error, 'server.auth.failed')
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

function onSortChange(payload: { prop: string; order: 'ascending' | 'descending' | null }) {
  if (!payload.order) {
    orderBy.value = null
    desc.value = false
  } else {
    orderBy.value = payload.prop
    desc.value = payload.order === 'descending'
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
  if (!dialog) {
    ElMessage.error(t('server.dbContent.authUnavailable'))
    return null
  }
  const code = await dialog.open(scene)
  if (!code) {
    ElMessage.warning(t('server.dbContent.authCancelled'))
  }
  return code
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
    ElMessage.success(t('server.dbContent.rowSaved'))
    await loadRows()
  } catch (error) {
    authRef.value?.fail(resolveErrorMessage(error, 'server.auth.failed'))
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
    ElMessage.success(t('server.dbContent.rowDeleted'))
    // 删除后若当页只剩 1 行且不在第一页,回退一页再刷新
    if (page.value > 0 && rowsData.value?.rows.length === 1) {
      page.value -= 1
    }
    await loadRows()
  } catch (error) {
    authRef.value?.fail(resolveErrorMessage(error, 'server.auth.failed'))
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
    queryError.value = resolveErrorMessage(error, 'server.auth.failed')
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
  // WHERE is not a safety proof: WHERE 1=1 is a full-table write. Every
  // UPDATE/DELETE/MERGE must name the target table, matching the backend.
  if (/^(DROP|TRUNCATE|DELETE|UPDATE|MERGE)\b/.test(upper)) return true
  if (
    /^ALTER\s+TABLE\b/.test(upper) &&
    /\b(DROP|RENAME|MODIFY|SET\s+UNUSED)\b/.test(upper)
  ) {
    return true
  }
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
    ddlError.value = resolveErrorMessage(error, 'server.auth.failed')
  } finally {
    ddlRunning.value = false
  }
}

void loadSchema()
</script>

<template>
  <div class="db-content-page phi-srv-page">
    <!-- 状态条:表数量与当前表行数是这一页最要紧的两个读数 -->
    <div class="phi-srv-strip">
      <ZHexUnit
        :label="t('server.dbContent.unitLabel')"
        :value="filteredSchema.length"
        size="lg"
        :hint="t('server.dbContent.unitTables', { count: filteredSchema.length })"
      />
      <div class="phi-srv-strip__cell">
        <div class="phi-srv-unit__body">
          <span class="phi-srv-unit__label">{{ t('server.dbContent.gridTitle') }}</span>
          <span class="phi-srv-unit__hint phi-srv-unit__hint--muted font-mono">{{
            selectedTable || '—'
          }}</span>
        </div>
      </div>
      <div class="phi-srv-strip__cell">
        <div class="phi-srv-unit__body">
          <span class="phi-srv-unit__label">{{ t('server.dbContent.totalLabel') }}</span>
          <span class="phi-srv-unit__hint">{{ rowsTotalDisplay() }}</span>
        </div>
      </div>
    </div>

    <div class="flex flex-1 min-h-0 gap-4 pb-2">
    <!-- 左:表清单 -->
    <ElCard class="w-72 shrink-0" body-class="table-list-body">
      <span class="phi-srv-subtitle mb-2 block tabular-nums">{{ t('server.dbContent.schemaCount', { count: filteredSchema.length }) }}</span>
      <ElInput
        v-model="schemaFilterInput"
        :placeholder="t('server.dbContent.searchPlaceholder')"
        clearable
        class="mb-2"
      />
      <div class="min-h-0 flex-1">
        <ElTable
          v-loading="schemaLoading"
          :data="filteredSchema"
          size="small"
          height="100%"
          highlight-current-row
          @row-click="(row: SchemaItem) => selectTable(row.name)"
        >
          <ElTableColumn prop="name" :label="t('server.dbContent.tableName')" min-width="140" show-overflow-tooltip />
          <ElTableColumn :label="t('server.dbContent.tableRows')" width="70" align="right">
            <template #default="{ row }">
              <span class="tabular-nums text-[12px] text-[var(--yg-text-tertiary)]">
                {{ row.num_rows === null ? '—' : t('server.dbContent.totalEstimate', { count: row.num_rows }) }}
              </span>
            </template>
          </ElTableColumn>
          <template #empty>
            <ElEmpty :image-size="48" :description="t('server.dbContent.queryEmpty')" />
          </template>
        </ElTable>
      </div>
    </ElCard>

    <!-- 右区 -->
    <div class="grid min-w-0 flex-1 grid-cols-1 grid-rows-[auto_auto_minmax(0,1fr)] items-stretch gap-4 xl:grid-cols-2 xl:grid-rows-[minmax(0,auto) minmax(0,auto) minmax(0,1fr)]">
      <!-- 列定义:占满左列纵向 -->
      <ElCard class="db-col-card xl:col-start-1 xl:row-span-2" body-class="db-col-body">
        <template #header>
          <span class="text-[14px] font-semibold text-[var(--yg-text-primary)]">{{ t('server.dbContent.columnsTitle') }}</span>
        </template>
        <div v-if="columnsData" class="min-h-0 flex-1">
          <ElTable
            v-loading="columnsLoading"
            :data="columnsData.columns"
            size="small"
            height="100%"
          >
            <ElTableColumn prop="name" :label="t('server.dbContent.colCol')" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="font-mono">{{ row.name }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="type" :label="t('server.dbContent.colColType')" width="110" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="text-[var(--yg-text-secondary)]">{{ row.type }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="length" :label="t('server.dbContent.colLen')" width="60" align="right">
              <template #default="{ row }">
                <span class="tabular-nums text-[var(--yg-text-secondary)]">{{ row.length ?? '—' }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="nullable" :label="t('server.dbContent.colNullable')" width="60" align="center">
              <template #default="{ row }">
                <span class="text-[var(--yg-text-secondary)]">{{ row.nullable ? t('server.dbContent.nullableYes') : '—' }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="primary_key" :label="t('server.dbContent.colPk')" width="60" align="center">
              <template #default="{ row }">
                <span v-if="row.primary_key" class="font-semibold text-[var(--yg-color-primary)]">PK</span>
                <span v-else class="text-[var(--yg-text-tertiary)]">—</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="default" :label="t('server.dbContent.colDefault')" min-width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="font-mono text-[12px] text-[var(--yg-text-secondary)]">{{ fmtCell(row.default as CellValue) }}</span>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>
        <div v-else class="flex flex-1 items-center justify-center">
          <ElEmpty :image-size="60" :description="columnsLoading ? t('server.database.loading') : t('server.dbContent.selTableHint')" />
        </div>
      </ElCard>

      <!-- SQL 查询(只读) + DDL:右列两个卡片 -->
      <ElCard class="xl:col-start-2 xl:row-start-1" body-class="db-panel-body">
        <template #header>
          <span class="text-[14px] font-semibold text-[var(--yg-text-primary)]">{{ t('server.dbContent.sqlTitle') }}</span>
        </template>
        <ElInput
          v-model="sqlText"
          type="textarea"
          :rows="3"
          :placeholder="t('server.dbContent.sqlPlaceholder')"
          class="sql-input"
        />
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <ElButton
            type="primary"
            :loading="sqlRunning"
            :disabled="sqlRunning"
            @click="executeQuery"
          >
            {{ t('server.dbContent.sqlRun') }}
          </ElButton>
          <span class="phi-srv-subtitle">{{ t('server.dbContent.sqlLimitLabel') }}</span>
          <ElInputNumber v-model="sqlLimit" :min="1" :max="500" size="small" class="!w-24" />
        </div>
        <ElAlert
          v-if="queryError"
          type="error"
          :title="queryError"
          :closable="false"
          class="mt-2"
        />
        <div v-if="queryResult" class="mt-2">
          <div class="mb-1 flex items-center gap-4">
            <span class="yg-text-xs yg-text-tertiary tabular-nums">{{ t('server.dbContent.queryElapsed', { ms: queryResult.elapsed_ms }) }}</span>
            <span v-if="queryResult.truncated" class="yg-text-xs yg-text-tertiary tabular-nums">
              {{ t('server.dbContent.queryTruncated', { limit: sqlLimit }) }}
            </span>
          </div>
          <ElTable :data="queryResult.rows" size="small" max-height="200" class="w-full">
            <ElTableColumn
              v-for="col in queryResult.columns"
              :key="col"
              :prop="col"
              :label="col"
              min-width="120"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <ElButton
                  v-if="isLob(row[col])"
                  link
                  size="small"
                  class="!font-mono !text-[12px]"
                  @click="openLobPreview(row[col] as LobValue)"
                >
                  {{ lobBadge(row[col] as LobValue) }}
                </ElButton>
                <span v-else class="font-mono text-[12px] text-[var(--yg-text-secondary)]">{{ fmtCell(row[col]) }}</span>
              </template>
            </ElTableColumn>
            <template #empty>
              <ElEmpty :image-size="40" :description="t('server.dbContent.queryEmpty')" />
            </template>
          </ElTable>
        </div>
      </ElCard>

      <ElCard class="xl:col-start-2 xl:row-start-2" body-class="db-panel-body">
        <template #header>
          <span class="text-[14px] font-semibold text-[var(--yg-text-primary)]">{{ t('server.dbContent.ddlTitle') }}</span>
        </template>
        <ElInput
          v-model="ddlText"
          type="textarea"
          :rows="3"
          :placeholder="t('server.dbContent.ddlPlaceholder')"
          class="sql-input"
        />
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <span class="phi-srv-subtitle">{{ t('server.dbContent.ddlConfirmLabel') }}</span>
          <ElInput
            v-model="ddlConfirm"
            :placeholder="t('server.dbContent.ddlConfirmPlaceholder')"
            size="small"
            class="!w-32 uppercase"
          />
          <ElButton
            type="danger"
            :loading="ddlRunning"
            :disabled="ddlRunning"
            @click="executeDdl"
          >
            {{ t('server.dbContent.ddlRun') }}
          </ElButton>
        </div>
        <ElAlert
          v-if="ddlError"
          type="error"
          :title="ddlError"
          :closable="false"
          class="mt-2"
        />
        <template v-else>
          <div v-if="ddlResult" class="mt-2 font-mono text-[13px] tabular-nums text-[var(--yg-text-secondary)]">
            {{ t('server.dbContent.ddlRowcount') }}: {{ ddlResult.rowcount === null ? '—' : ddlResult.rowcount }}
            · {{ t('server.dbContent.ddlSummary') }}: {{ ddlResult.summary }}
          </div>
          <div v-else class="mt-2 phi-srv-dim">{{ ddlNote || t('server.dbContent.ddlNone') }}</div>
        </template>
        <p class="mt-2 phi-srv-dim">{{ t('server.dbContent.ddlCommitted') }}</p>
      </ElCard>

      <!-- 数据网格 -->
      <ElCard class="xl:col-span-2" body-class="db-grid-body">
        <div class="flex flex-wrap items-center justify-between gap-2 pb-2">
          <div class="flex items-center gap-3">
            <span class="phi-srv-subtitle">{{ t('server.dbContent.gridTitle') }}</span>
            <span v-if="selectedTable" class="font-mono text-[14px] font-semibold text-[var(--yg-text-primary)]">
              {{ selectedTable }}
            </span>
            <span v-if="selectedTable && !hasPk" class="phi-srv-dim">
              {{ t('server.dbContent.noPkNote') }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <ElButton size="small" :icon="Plus" :disabled="!selectedTable || submitting" @click="openInsert">
              {{ t('server.dbContent.insert') }}
            </ElButton>
            <span v-if="rowsData" class="phi-srv-subtitle tabular-nums">
              {{ t('server.dbContent.totalLabel') }} {{ rowsTotalDisplay() }}
            </span>
            <ElButton size="small" :disabled="page <= 0 || rowsLoading" @click="gotoPage(page - 1)">
              ‹ {{ t('server.dbContent.pagePrev') }}
            </ElButton>
            <span v-if="rowsData" class="phi-srv-subtitle tabular-nums">
              {{ t('server.dbContent.pageInfo', { page: page + 1, pages: Math.max(1, rowsPages()) }) }}
            </span>
            <ElButton
              size="small"
              :disabled="rowsLoading || !rowsData || !rowsData.has_more"
              @click="gotoPage(page + 1)"
            >
              {{ t('server.dbContent.pageNext') }} ›
            </ElButton>
          </div>
        </div>

        <ElAlert
          v-if="dataErrorNote"
          type="error"
          :title="dataErrorNote"
          :closable="false"
          class="mb-2"
        />

        <div v-if="rowsData" class="min-h-0 flex-1">
          <ElTable
            v-loading="rowsLoading"
            :data="rowsData.rows"
            height="100%"
            class="w-full"
            @sort-change="onSortChange"
          >
            <ElTableColumn
              v-for="col in columnsData?.columns ?? []"
              :key="col.name"
              :prop="col.name"
              :label="col.name"
              sortable="custom"
              min-width="140"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                <ElButton
                  v-if="isLob(row[col.name] as CellValue)"
                  link
                  size="small"
                  class="!font-mono !text-[12px]"
                  @click="openLobPreview(row[col.name] as LobValue)"
                >
                  {{ lobBadge(row[col.name] as LobValue) }}
                </ElButton>
                <span v-else class="font-mono text-[12px] text-[var(--yg-text-secondary)]">{{ fmtCell(row[col.name] as CellValue) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn
              v-if="hasPk"
              :label="t('server.dbContent.rowActions')"
              width="150"
              align="right"
              fixed="right"
            >
              <template #default="{ row }">
                <ElButton text bg size="small" :disabled="submitting" @click="openEdit(row as Record<string, CellValue>)">
                  {{ t('server.dbContent.edit') }}
                </ElButton>
                <ElButton text bg size="small" type="danger" :disabled="submitting" @click="doDelete(row as Record<string, CellValue>)">
                  {{ t('server.dbContent.del') }}
                </ElButton>
              </template>
            </ElTableColumn>
            <template #empty>
              <ElEmpty
                :image-size="56"
                :description="rowsData && rowsData.rows.length === 0 ? t('server.dbContent.gridEmpty') : t('server.dbContent.selTableHint')"
              />
            </template>
          </ElTable>
        </div>
        <div v-else class="flex flex-1 items-center justify-center">
          <ElEmpty :image-size="56" :description="t('server.dbContent.selTableHint')" />
        </div>
      </ElCard>
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
          <div class="yg-text-xs yg-text-tertiary mb-1 font-mono">
            {{ field.name }}
            <span class="normal-case">· {{ field.type }}{{ field.isPk ? ' · PK' : field.nullable ? '' : ' · NOT NULL' }}</span>
          </div>
          <ElInput
            v-if="fieldMode(field.type) !== 'textarea'"
            v-model="field.text"
            :type="fieldMode(field.type)"
            :placeholder="t('server.dbContent.nullHint')"
          />
          <ElInput
            v-else
            v-model="field.text"
            type="textarea"
            :rows="3"
            :placeholder="t('server.dbContent.nullHint')"
          />
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <ElButton @click="rowDialogVisible = false">{{ t('server.auth.cancel') }}</ElButton>
          <ElButton type="primary" :loading="submitting" @click="submitRowChange">
            {{ t('server.dbContent.proceedAuth') }}
          </ElButton>
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
      <pre class="max-h-[50vh] overflow-auto whitespace-pre-wrap break-all rounded-md border border-[var(--yg-border-default)] bg-[var(--yg-bg-surface-sunken)] p-3 font-mono text-[13px] leading-5 text-[var(--yg-text-primary)]">{{ lobPreviewText }}</pre>
      <p class="mt-2 phi-srv-dim">{{ t('server.dbContent.lobTitle') }}</p>
    </ElDialog>

    <ZSystemAuthDialog ref="authRef" />
    </div>
  </div>
</template>

<style scoped>
.table-list-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.sql-input {
  font-family: ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace;
}
</style>
