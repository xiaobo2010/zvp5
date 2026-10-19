<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { AxiosError } from 'axios'
import {
  listFiles,
  makeDir,
  uploadFile,
  renamePath,
  deleteEntry,
  chmodEntry,
  downloadFile,
  type FileEntry
} from '@/api/system/files.ts'
import ZSystemAuthDialog from '@/components/system/ZSystemAuthDialog.vue'
import ZPagination from '@/components/utils/ZPagination.vue'
import {
  Refresh,
  FolderAdd,
  Upload,
  Delete,
  Download,
  EditPen,
  Key,
  FolderOpened,
  Back,
  Location
} from '@element-plus/icons-vue'

const { t } = useI18n()

const DEFAULT_PAGE_SIZE = 200
const QUICK_DIRS = ['/', '/www', '/www/zvms4', '/etc', '/home', '/var/log', '/tmp']

const currentPath = ref('/')
const pathInput = ref('/')
const entries = ref<FileEntry[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(DEFAULT_PAGE_SIZE)
const loading = ref(false)

const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

function errorMessageOf(error: unknown): string {
  const err = error as AxiosError<{ detail?: unknown }>
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  return t('server.auth.failed')
}

function axiosStatus(error: unknown): number {
  const err = error as AxiosError
  return err?.response?.status ?? 0
}

function fmtSize(size: number, isDir: boolean): string {
  if (isDir) return '—'
  if (size < 1024) return `${size} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = size
  let unit = 'B'
  for (const u of units) {
    if (value < 1024) break
    value /= 1024
    unit = u
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${unit}`
}

function fmtMode(mode: number): string {
  return mode.toString(8).padStart(3, '0')
}

function fmtTime(mtime: number): string {
  const date = new Date(mtime * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function typeLabel(entry: FileEntry): string {
  if (entry.type === 'dir') return t('server.files.typeDir')
  if (entry.type === 'file') return t('server.files.typeFile')
  if (entry.type === 'symlink') return t('server.files.typeSymlink')
  return t('server.files.typeOther')
}

async function load() {
  loading.value = true
  try {
    const offset = (page.value - 1) * pageSize.value
    const res = await listFiles(currentPath.value, offset, pageSize.value)
    const data = res.data?.data
    currentPath.value = data.path
    pathInput.value = data.path
    entries.value = data.entries
    total.value = data.total
  } catch (error) {
    entries.value = []
    total.value = 0
    ElMessage({
      message: errorMessageOf(error),
      type: 'error',
      grouping: true,
      plain: true
    })
  } finally {
    loading.value = false
  }
}

async function jumpPath() {
  let raw = pathInput.value.trim()
  if (!raw) raw = '/'
  if (!raw.startsWith('/')) {
    ElMessage({ message: t('server.files.absolutePathOnly'), type: 'warning', grouping: true, plain: true })
    return
  }
  currentPath.value = raw
  page.value = 1
  await load()
}

function goQuick(dir: string) {
  pathInput.value = dir
  void jumpPath()
}

function goCrumb(path: string | null) {
  if (path === null) return
  pathInput.value = path
  void jumpPath()
}

function goParent() {
  const stripped = currentPath.value.replace(/\/+$/, '') || '/'
  const parent = stripped.slice(0, stripped.lastIndexOf('/')) || '/'
  pathInput.value = parent
  void jumpPath()
}

function entryPath(name: string): string {
  return currentPath.value === '/' ? `/${name}` : `${currentPath.value}/${name}`
}

function openEntry(entry: FileEntry) {
  if (entry.type === 'dir' || entry.type === 'symlink') {
    pathInput.value = entryPath(entry.name)
    void jumpPath()
  }
}

function crumbs(): { name: string; path: string | null }[] {
  const parts = currentPath.value.split('/').filter(Boolean)
  const acc: { name: string; path: string | null }[] = [{ name: '/', path: null }]
  let prefix = ''
  for (const part of parts) {
    prefix += '/' + part
    acc.push({ name: part, path: prefix })
  }
  return acc
}

// —— 写操作统一模式:先双人授权,拿码后带 totp_code 发请求 —— //

async function openAuth(scene: string): Promise<string | null> {
  const dialog = authRef.value
  if (!dialog) {
    ElMessage.error(t('server.files.authUnavailable'))
    return null
  }
  const code = await dialog.open(scene)
  if (!code) {
    ElMessage.warning(t('server.files.authCancelled'))
  }
  return code
}

async function doMkdir() {
  let name: string
  try {
    const result = await ElMessageBox.prompt(t('server.files.mkdirNamePrompt'), t('server.files.mkdirPromptTitle'), {
      inputPattern: /\S+/,
      inputErrorMessage: t('server.files.mkdirNamePrompt')
    })
    name = result.value.trim()
  } catch {
    return
  }
  const target = entryPath(name)
  const code = await openAuth(t('server.files.authSceneMkdir', { path: target }))
  if (!code) return
  try {
    await makeDir(target, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.mkdirDone'))
    await load()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
  }
}

function pickUploadFile() {
  const input = document.createElement('input')
  input.type = 'file'
  input.style.display = 'none'
  input.onchange = () => {
    const file = input.files?.[0]
    input.remove()
    if (file) void onUpload(file)
  }
  input.click()
}

async function onUpload(file: File) {
  const code = await openAuth(
    t('server.files.authSceneUpload', { name: file.name, path: currentPath.value })
  )
  if (!code) return
  try {
    await uploadFile(currentPath.value, file, code, false)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.uploadDone'))
    await load()
  } catch (error) {
    const status = axiosStatus(error)
    if (status !== 409) {
      authRef.value?.fail(errorMessageOf(error))
      return
    }
    // 409 同名:询问是否覆盖。注意携带同一动态码重试会触发防重放 409,
    // 所以覆盖路径必须重新双人授权。
    authRef.value?.succeed()
    try {
      await ElMessageBox.confirm(t('server.files.overwriteConfirm', { name: file.name }), t('server.files.title'), {
        type: 'warning'
      })
    } catch {
      return
    }
    const retryCode = await openAuth(
      t('server.files.authSceneUpload', { name: file.name, path: currentPath.value })
    )
    if (!retryCode) return
    try {
      await uploadFile(currentPath.value, file, retryCode, true)
      authRef.value?.succeed()
      ElMessage.success(t('server.files.uploadDone'))
      await load()
    } catch (retryError) {
      authRef.value?.fail(errorMessageOf(retryError))
    }
  }
}

async function doRename(entry: FileEntry) {
  let newName: string
  try {
    const result = await ElMessageBox.prompt(t('server.files.renamePromptHint'), t('server.files.renamePromptTitle'), {
      inputValue: entry.name,
      inputPattern: /\S+/,
      inputErrorMessage: t('server.files.renamePromptHint')
    })
    newName = result.value.trim()
  } catch {
    return
  }
  if (newName === entry.name) return
  const from = entryPath(entry.name)
  const to = entryPath(newName)
  const code = await openAuth(t('server.files.authSceneRename', { from, to }))
  if (!code) return
  try {
    await renamePath(from, to, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.renameDone'))
    await load()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
  }
}

async function doDelete(entry: FileEntry, recursive: boolean, reuseCode?: string | null) {
  const path = entryPath(entry.name)
  let code: string | null = reuseCode ?? null
  if (!code) {
    code = await openAuth(
      t('server.files.authSceneDelete', {
        path,
        recursive: recursive
          ? t('server.files.authSceneRecursive')
          : t('server.files.authSceneSingle')
      })
    )
  }
  if (!code) return
  try {
    await deleteEntry(path, recursive, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.deleteDone'))
    await load()
  } catch (error) {
    const status = axiosStatus(error)
    if (status === 409 && !recursive) {
      // 后端 delete_path:非空目录 + 非递归 → 409 "目录非空,需递归删除"
      authRef.value?.succeed()
      try {
        await ElMessageBox.confirm(t('server.files.deleteRecursiveConfirm'), t('server.files.delete'), {
          type: 'warning'
        })
      } catch {
        return
      }
      // 防重放:同一动态码不能二次使用,递归重试需重新双人授权
      await doDelete(entry, true)
      return
    }
    authRef.value?.fail(errorMessageOf(error))
  }
}

function onDeleteClick(entry: FileEntry) {
  ElMessageBox.confirm(t('server.files.deleteConfirm', { name: entry.name }), t('server.files.delete'), {
    type: 'warning'
  })
    .then(() => doDelete(entry, false))
    .catch(() => undefined)
}

async function doChmod(entry: FileEntry) {
  let modeText: string
  try {
    const result = await ElMessageBox.prompt(t('server.files.chmodModeHint'), t('server.files.chmodPromptTitle'), {
      inputValue: fmtMode(entry.mode),
      inputPattern: /^[0-7]{3,4}$/,
      inputPlaceholder: '644',
      inputErrorMessage: t('server.files.chmodModeHint')
    })
    modeText = result.value.trim()
  } catch {
    return
  }
  const mode = parseInt(modeText, 8)
  const path = entryPath(entry.name)
  const code = await openAuth(
    t('server.files.authSceneChmod', { path, mode: fmtMode(mode) })
  )
  if (!code) return
  try {
    await chmodEntry(path, mode, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.chmodDone'))
    await load()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
  }
}

async function doDownload(entry: FileEntry) {
  const path = entryPath(entry.name)
  try {
    const res = await downloadFile(path)
    const url = URL.createObjectURL(res.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = entry.name
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 3000)
  } catch {
    ElMessage({
      message: t('server.files.downloadFailed'),
      type: 'error',
      grouping: true,
      plain: true
    })
  }
}

function onPageChange() {
  void load()
}

void load()
</script>

<template>
  <div class="flex flex-col gap-4 pb-2">
    <div class="flex h-[calc(100vh-300px)] min-h-[480px] items-stretch gap-4">
      <!-- 左:快捷目录 -->
      <ElCard class="w-64 shrink-0" body-class="quick-body">
        <span class="z-label mb-2 block">{{ t('server.files.quickDirs') }}</span>
        <ElMenu class="quick-menu" :default-active="currentPath" @select="goQuick">
          <ElMenuItem
            v-for="dir in QUICK_DIRS"
            :key="dir"
            :index="dir"
            class="font-mono !text-[13px]"
          >
            <ElIcon :size="15" class="mr-1"><FolderOpened /></ElIcon>
            {{ dir }}
          </ElMenuItem>
        </ElMenu>
      </ElCard>

      <div class="flex min-w-0 flex-1 flex-col gap-4">
        <!-- 工具栏 -->
        <ElCard body-class="!px-4 !py-2.5">
          <div class="flex flex-wrap items-center gap-2">
            <ElButton :icon="Back" @click="goParent">{{ t('server.files.parent') }}</ElButton>
            <ElInput
              v-model="pathInput"
              class="min-w-[220px] max-w-[560px] flex-1 font-mono"
              :prefix-icon="Location"
              @keyup.enter="jumpPath"
            />
            <ElButton type="primary" plain :icon="Refresh" @click="load">
              {{ t('server.files.refresh') }}
            </ElButton>
            <ElButton :icon="FolderAdd" @click="doMkdir">{{ t('server.files.mkdir') }}</ElButton>
            <ElButton :icon="Upload" @click="pickUploadFile">{{ t('server.files.upload') }}</ElButton>
            <span class="z-label">{{ t('server.files.uploadLimit') }}</span>
          </div>
        </ElCard>

        <!-- 面包屑 + 文件表 + 分页 -->
        <ElCard class="files-card flex-1" body-class="files-body">
          <div class="flex flex-wrap items-center gap-x-3 pb-2">
            <ElBreadcrumb class="min-w-0 flex-1">
              <ElBreadcrumbItem
                v-for="(crumb, index) in crumbs()"
                :key="crumb.name"
                :class="index === 0 ? 'font-mono' : ''"
                @click="goCrumb(crumb.path)"
              >
                {{ crumb.name }}
              </ElBreadcrumbItem>
            </ElBreadcrumb>
            <span class="z-label tabular-nums">{{ t('server.files.totalEntries', { count: total }) }}</span>
          </div>

          <div class="min-h-0 flex-1">
            <ElTable
              v-loading="loading"
              :data="entries"
              row-key="name"
              stripe
              height="100%"
              class="files-table"
            >
              <ElTableColumn prop="name" :label="t('server.files.colName')" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">
                  <span
                    class="name-cell"
                    role="button"
                    tabindex="0"
                    @click="openEntry(row)"
                    @keyup.enter="openEntry(row)"
                  >
                    {{ row.name }}<span v-if="row.type === 'dir'" class="text-[var(--zv-label)]">/</span>
                  </span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="size" :label="t('server.files.colSize')" width="100">
                <template #default="{ row }">
                  <span class="tabular-nums text-[var(--zv-text-sub)]">{{ fmtSize(row.size, row.type === 'dir') }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="mtime" :label="t('server.files.colMtime')" width="150">
                <template #default="{ row }">
                  <span class="tabular-nums text-[var(--zv-text-sub)]">{{ fmtTime(row.mtime) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="type" :label="t('server.files.colType')" width="90">
                <template #default="{ row }">
                  <span class="text-[var(--zv-text-sub)]">{{ typeLabel(row) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn :label="t('server.files.colActions')" width="250" align="right" fixed="right">
                <template #default="{ row }">
                  <ElButton
                    v-if="row.type === 'file'"
                    text
                    bg
                    size="small"
                    :icon="Download"
                    @click="doDownload(row as FileEntry)"
                  >
                    {{ t('server.files.download') }}
                  </ElButton>
                  <ElButton text bg size="small" :icon="EditPen" @click="doRename(row as FileEntry)">
                    {{ t('server.files.rename') }}
                  </ElButton>
                  <ElButton text bg size="small" :icon="Key" @click="doChmod(row as FileEntry)">
                    {{ t('server.files.chmod') }}
                  </ElButton>
                  <ElButton text bg size="small" type="danger" :icon="Delete" @click="onDeleteClick(row as FileEntry)">
                    {{ t('server.files.delete') }}
                  </ElButton>
                </template>
              </ElTableColumn>
              <template #empty>
                <ElEmpty :image-size="64" :description="t('server.files.loadFailed')" />
              </template>
            </ElTable>
          </div>

          <div class="flex items-center justify-end pt-1">
            <ZPagination
              v-model:current-page="page"
              v-model:page-size="pageSize"
              :total="total"
              :page-sizes="[50, 100, 200]"
              @change="onPageChange"
            />
          </div>
        </ElCard>
      </div>
    </div>
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
.quick-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.quick-menu {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  border-right: none;
}

.quick-menu :deep(.el-menu-item.is-active) {
  font-weight: 600;
}

.name-cell {
  border: 0;
  background: transparent;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.name-cell:hover {
  color: var(--zv-accent);
  text-decoration: underline;
}

.files-card {
  display: flex;
  flex-direction: column;
}

.files-card :deep(.el-card__body.files-body) {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 12px 16px 0;
}

.files-table :deep(.el-table__cell) {
  line-height: 20px;
}
</style>
