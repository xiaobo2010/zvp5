<script setup lang="ts">
import { computed, ref } from 'vue'
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
import { Refresh, FolderAdd, Upload, Delete, Download, EditPen, Key, FolderOpened, CaretRight } from '@element-plus/icons-vue'

const { t } = useI18n()

const PAGE_SIZE = 500
const QUICK_DIRS = ['/www', '/home', '/tmp', '/opt', '/srv', '/var/www', '/var/log', '/media', '/mnt']

const currentPath = ref('/')
const pathInput = ref('/')
const entries = ref<FileEntry[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const selected = ref<FileEntry | null>(null)
const uploadInput = ref<HTMLInputElement>()

const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

const pages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const crumbs = computed(() => {
  const parts = currentPath.value.split('/').filter(Boolean)
  const acc: { name: string; path: string }[] = [{ name: '/', path: '/' }]
  let prefix = ''
  for (const part of parts) {
    prefix += '/' + part
    acc.push({ name: part, path: prefix })
  }
  return acc
})

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
    const offset = (page.value - 1) * PAGE_SIZE
    const res = await listFiles(currentPath.value, offset, PAGE_SIZE)
    const data = res.data?.data
    currentPath.value = data.path
    pathInput.value = data.path
    entries.value = data.entries
    total.value = data.total
  } catch {
    entries.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function refresh() {
  await load()
}

function rowDblClick(entry: FileEntry) {
  if (entry.type === 'dir' || entry.type === 'symlink') {
    pathInput.value = entryPath(entry.name)
    void jumpPath()
  }
}

async function jumpPath() {
  let raw = pathInput.value.trim()
  if (!raw) raw = '/'
  if (!raw.startsWith('/')) {
    ElMessage({ message: '仅接受绝对路径', type: 'warning', grouping: true, plain: true })
    return
  }
  currentPath.value = raw
  page.value = 1
  selected.value = null
  await load()
}

function goQuick(dir: string) {
  pathInput.value = dir
  void jumpPath()
}

function goCrumb(path: string) {
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

// —— 写操作统一模式:先双人授权,拿码后带 totp_code 发请求 —— //

async function openAuth(scene: string): Promise<string | null> {
  const dialog = authRef.value
  if (!dialog) return null
  return dialog.open(scene)
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
    await load()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
  }
}

function pickUploadFile() {
  uploadInput.value?.click()
}

async function onUploadChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const code = await openAuth(
    t('server.files.authSceneUpload', { name: file.name, path: currentPath.value })
  )
  if (!code) return
  try {
    await uploadFile(currentPath.value, file, code, false)
    authRef.value?.succeed()
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
      await load()
    } catch (retryError) {
      authRef.value?.fail(errorMessageOf(retryError))
    }
  }
}

async function doRename() {
  const entry = selected.value
  if (!entry) return
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
    selected.value = null
    await load()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
  }
}

async function doDelete(recursive: boolean, reuseCode?: string | null) {
  const entry = selected.value
  if (!entry) return
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
    selected.value = null
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
      selected.value = entry
      await doDelete(true)
      return
    }
    authRef.value?.fail(errorMessageOf(error))
  }
}

function onDeleteClick() {
  const entry = selected.value
  if (!entry) return
  ElMessageBox.confirm(t('server.files.deleteConfirm', { name: entry.name }), t('server.files.delete'), {
    type: 'warning'
  })
    .then(() => doDelete(false))
    .catch(() => undefined)
}

async function doChmod() {
  const entry = selected.value
  if (!entry) return
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
    await load()
  } catch (error) {
    authRef.value?.fail(errorMessageOf(error))
  }
}

async function doDownload() {
  const entry = selected.value
  if (!entry) return
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

function pageChange(target: number) {
  page.value = target
  selected.value = null
  void load()
}

void load()
</script>

<template>
  <div class="flex flex-col gap-3 pb-2">
    <!-- 页面标题行 -->
    <div class="flex flex-wrap items-end justify-between gap-2 px-1">
      <div class="flex flex-col gap-0.5">
        <h2 class="m-0 text-[22px] font-semibold leading-7 text-[var(--zv-text)]">{{ t('server.files.title') }}</h2>
        <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.files.protectedNote') }}</span>
      </div>
    </div>

    <div class="flex h-[calc(100vh-330px)] min-h-[440px] gap-3">
      <!-- 左:快捷目录 + 面包屑 -->
      <aside class="flex w-64 shrink-0 flex-col gap-3 border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-4">
        <div>
          <div class="d-label mb-2">{{ t('server.files.quickDirs') }}</div>
          <button
            v-for="dir in QUICK_DIRS"
            :key="dir"
            class="block w-full truncate border-0 bg-transparent px-1 py-2 text-left text-[14px] text-[var(--zv-text-sub)] transition-colors hover:bg-[var(--zv-surface-alt)] hover:text-[var(--zv-text)]"
            @click="goQuick(dir)"
          >
            <ElIcon :size="18" class="mr-1.5 -mt-0.5 align-middle"><FolderOpened /></ElIcon>{{ dir }}
          </button>
        </div>
      </aside>

      <div class="flex min-w-0 flex-1 flex-col gap-2">
        <!-- 工具栏 -->
        <div class="flex flex-wrap items-center gap-2 border border-[var(--zv-hairline)] bg-[var(--zv-surface)] px-4 py-2">
          <button
            class="flex items-center gap-1 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            style="height: 34px"
            @click="goParent"
          >
            ..
          </button>
          <input
            v-model="pathInput"
            class="min-w-[240px] flex-1 border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-3 text-[14px] text-[var(--zv-text)] outline-none focus:border-[var(--zv-hairline-strong)]"
            style="height: 34px"
            @keyup.enter="jumpPath"
          />
          <button
            class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            style="height: 34px"
            @click="jumpPath"
          >
            <ElIcon :size="18" class="-ml-1"><CaretRight /></ElIcon>
            {{ t('server.files.go') }}
          </button>
          <button
            class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            style="height: 34px"
            @click="refresh"
          >
            <ElIcon :size="18" class="-ml-1"><Refresh /></ElIcon>
            {{ t('server.files.refresh') }}
          </button>
          <span class="h-5 w-px bg-[var(--zv-hairline)]"></span>
          <button
            class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            style="height: 34px"
            @click="doMkdir"
          >
            <ElIcon :size="18" class="-ml-1"><FolderAdd /></ElIcon>
            {{ t('server.files.mkdir') }}
          </button>
          <button
            class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            style="height: 34px"
            @click="pickUploadFile"
          >
            <ElIcon :size="18" class="-ml-1"><Upload /></ElIcon>
            {{ t('server.files.upload') }}
          </button>
          <input ref="uploadInput" type="file" class="hidden" @change="onUploadChange" />
          <span class="text-[12px] text-[var(--zv-label)]">{{ t('server.files.uploadLimit') }}</span>
          <span class="h-5 w-px bg-[var(--zv-hairline)]"></span>
          <template v-if="selected">
            <button
              class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
              style="height: 34px"
              @click="doRename"
            >
              <ElIcon :size="18" class="-ml-1"><EditPen /></ElIcon>
              {{ t('server.files.rename') }}
            </button>
            <button
              class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
              style="height: 34px"
              @click="doChmod"
            >
              <ElIcon :size="18" class="-ml-1"><Key /></ElIcon>
              {{ t('server.files.chmod') }}
            </button>
            <button
              v-if="selected.type === 'file'"
              class="flex items-center gap-1.5 border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 text-[14px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
              style="height: 34px"
              @click="doDownload"
            >
              <ElIcon :size="18" class="-ml-1"><Download /></ElIcon>
              {{ t('server.files.download') }}
            </button>
            <button
              class="flex items-center gap-1.5 border border-[#b42318] bg-[var(--zv-surface)] px-3 text-[14px] text-[#b42318] hover:bg-[var(--zv-surface-alt)] dark:text-[#ff8a7a]"
              style="height: 34px"
              @click="onDeleteClick"
            >
              <ElIcon :size="18" class="-ml-1"><Delete /></ElIcon>
              {{ t('server.files.delete') }}
            </button>
          </template>
          <span v-else class="text-[12px] text-[var(--zv-label)]">{{ t('server.files.noSelection') }}</span>
        </div>

        <!-- 面包屑 -->
        <div class="flex flex-wrap items-center gap-1 px-1 text-[14px]">
          <button
            v-for="(crumb, index) in crumbs"
            :key="crumb.path"
            class="border-0 bg-transparent py-1 text-[14px] hover:underline"
            :class="index === crumbs.length - 1 ? 'font-medium text-[var(--zv-text)]' : 'text-[var(--zv-text-sub)]'"
            @click="goCrumb(crumb.path)"
          >
            {{ crumb.name }}
          </button>
          <span v-if="selected" class="ml-3 text-[12px] text-[var(--zv-label)]">
            {{ t('server.files.selection', { name: selected.name }) }}
            <template v-if="selected.is_symlink && selected.target">
              · {{ t('server.files.symlinkTarget', { target: selected.target }) }}
            </template>
          </span>
        </div>

        <!-- 条目表 -->
        <div class="min-h-0 flex-1 overflow-auto border border-[var(--zv-hairline)] bg-[var(--zv-surface)]">
          <table class="f-table w-full border-collapse text-[14px] tabular-nums">
            <thead class="sticky top-0 z-10 bg-[var(--zv-surface-alt)]">
              <tr class="border-b border-[var(--zv-hairline-strong)]">
                <th class="d-label px-4 py-2 text-left font-normal">{{ t('server.files.colName') }}</th>
                <th class="d-label px-4 py-2 text-left font-normal">{{ t('server.files.colType') }}</th>
                <th class="d-label px-4 py-2 text-right font-normal">{{ t('server.files.colSize') }}</th>
                <th class="d-label px-4 py-2 text-left font-normal">{{ t('server.files.colMode') }}</th>
                <th class="d-label px-4 py-2 text-left font-normal">{{ t('server.files.colMtime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-if="currentPath !== '/'"
                class="cursor-pointer border-b border-[var(--zv-hairline)] hover:bg-[var(--zv-surface-alt)]"
                @click="goParent"
              >
                <td class="px-4" colspan="5">..</td>
              </tr>
              <tr
                v-for="entry in entries"
                :key="entry.name"
                class="cursor-pointer border-b border-[var(--zv-hairline)] hover:bg-[var(--zv-surface-alt)]"
                :class="selected?.name === entry.name ? 'bg-[var(--zv-surface-alt)]' : ''"
                @click="selected = entry"
                @dblclick="rowDblClick(entry)"
              >
                <td class="px-4 py-2 text-[var(--zv-text)]">
                  <span :class="entry.type === 'dir' ? 'font-medium' : ''">{{ entry.name }}</span>
                  <span v-if="entry.type === 'dir'" class="ml-1 text-[var(--zv-label)]">/</span>
                </td>
                <td class="px-4 py-2 text-[13px] text-[var(--zv-text-sub)]">{{ typeLabel(entry) }}</td>
                <td class="px-4 py-2 text-right text-[13px] tabular-nums text-[var(--zv-text-sub)]">
                  {{ fmtSize(entry.size, entry.type === 'dir') }}
                </td>
                <td class="px-4 py-2 text-[13px] tabular-nums text-[var(--zv-text-sub)]">{{ fmtMode(entry.mode) }}</td>
                <td class="px-4 py-2 text-[13px] tabular-nums text-[var(--zv-text-sub)]">{{ fmtTime(entry.mtime) }}</td>
              </tr>
              <tr v-if="!loading && entries.length === 0">
                <td class="px-4 py-6 text-center text-[13px] text-[var(--zv-label)]" colspan="5">
                  {{ t('server.files.loadFailed') }}
                </td>
              </tr>
              <tr v-if="loading">
                <td class="px-4 py-6 text-center text-[13px] text-[var(--zv-label)]" colspan="5">…</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div class="flex items-center justify-between border border-[var(--zv-hairline)] bg-[var(--zv-surface)] px-4 py-2 text-[13px] text-[var(--zv-label)]">
          <span>{{ t('server.files.totalEntries', { count: total }) }}</span>
          <span class="tabular-nums">{{ t('server.files.pageInfo', { page, pages }) }}</span>
          <div class="flex gap-2">
            <button
              class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 py-1 text-[13px] tabular-nums text-[var(--zv-text)] disabled:opacity-40 hover:bg-[var(--zv-surface-alt)]"
              :disabled="page <= 1 || loading"
              @click="pageChange(page - 1)"
            >
              {{ t('server.files.pagePrev') }}
            </button>
            <button
              class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-3 py-1 text-[13px] tabular-nums text-[var(--zv-text)] disabled:opacity-40 hover:bg-[var(--zv-surface-alt)]"
              :disabled="page >= pages || loading"
              @click="pageChange(page + 1)"
            >
              {{ t('server.files.pageNext') }}
            </button>
          </div>
        </div>
      </div>
    </div>
    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>

<style scoped>
/* 本页局部 token:建议后续由 flat.css 统一收口 */
.d-label {
  font-size: 12px;
  line-height: 1.35;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--zv-label);
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}

.f-table tbody tr:hover td,
.f-table tbody tr:hover {
  background: var(--zv-surface-alt);
}
</style>
