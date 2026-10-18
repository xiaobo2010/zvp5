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

function goParent() {
  const stripped = currentPath.value.replace(/\/+$/, '') || '/'
  const parent = stripped.slice(0, stripped.lastIndexOf('/')) || '/'
  pathInput.value = parent
  void jumpPath()
}

async function refresh() {
  await load()
}

function rowDblClick(row: FileEntry) {
  if (row.type === 'dir' || row.type === 'symlink') {
    const joined = currentPath.value === '/' ? `/${row.name}` : `${currentPath.value}/${row.name}`
    pathInput.value = joined
    void jumpPath()
  }
}

function rowClick(row: FileEntry) {
  selected.value = row
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
  <div class="flex h-[calc(100vh-220px)] min-h-[420px] gap-2">
    <aside class="flex w-44 shrink-0 flex-col gap-3 border border-[var(--zv-hairline)] bg-[var(--zv-surface)] p-2">
      <div>
        <div class="z-label mb-1">{{ t('server.files.quickDirs') }}</div>
        <button
          v-for="dir in QUICK_DIRS"
          :key="dir"
          class="block w-full truncate border-0 bg-transparent px-1 py-1 text-left text-[12px] text-[var(--zv-text-sub)] hover:bg-[var(--zv-surface-alt)] hover:text-[var(--zv-text)]"
          @click="goQuick(dir)"
        >
          {{ dir }}
        </button>
      </div>
      <div class="mt-auto border-t border-[var(--zv-hairline)] pt-2 text-[11px] leading-4 text-[var(--zv-label)]">
        {{ t('server.files.protectedNote') }}
      </div>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col gap-2">
      <div class="flex flex-wrap items-center gap-2 border border-[var(--zv-hairline)] bg-[var(--zv-surface)] px-2 py-1.5">
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
          @click="goParent"
        >
          ..
        </button>
        <input
          v-model="pathInput"
          class="min-w-[220px] flex-1 border border-[var(--zv-hairline)] bg-[var(--zv-surface-alt)] px-2 py-1 text-[13px] text-[var(--zv-text)] outline-none focus:border-[var(--zv-hairline-strong)]"
          @keyup.enter="jumpPath"
        />
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
          @click="jumpPath"
        >
          {{ t('server.files.go') }}
        </button>
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
          @click="refresh"
        >
          {{ t('server.files.refresh') }}
        </button>
        <span class="h-4 w-px bg-[var(--zv-hairline)]"></span>
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
          @click="doMkdir"
        >
          {{ t('server.files.mkdir') }}
        </button>
        <button
          class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
          @click="pickUploadFile"
        >
          {{ t('server.files.upload') }}
        </button>
        <input ref="uploadInput" type="file" class="hidden" @change="onUploadChange" />
        <span class="text-[11px] text-[var(--zv-label)]">{{ t('server.files.uploadLimit') }}</span>
        <span class="h-4 w-px bg-[var(--zv-hairline)]"></span>
        <template v-if="selected">
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            @click="doRename"
          >
            {{ t('server.files.rename') }}
          </button>
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            @click="doChmod"
          >
            {{ t('server.files.chmod') }}
          </button>
          <button
            v-if="selected.type === 'file'"
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-text)] hover:bg-[var(--zv-surface-alt)]"
            @click="doDownload"
          >
            {{ t('server.files.download') }}
          </button>
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[#b42318] hover:bg-[var(--zv-surface-alt)] dark:text-[#ff8a7a]"
            @click="onDeleteClick"
          >
            {{ t('server.files.delete') }}
          </button>
        </template>
        <span v-else class="text-[11px] text-[var(--zv-label)]">{{ t('server.files.noSelection') }}</span>
      </div>

      <span v-if="selected" class="text-[11px] text-[var(--zv-label)]">
        {{ t('server.files.selection', { name: selected.name }) }}
        <template v-if="selected.is_symlink && selected.target">
          · {{ t('server.files.symlinkTarget', { target: selected.target }) }}
        </template>
      </span>

      <div class="min-h-0 flex-1 overflow-auto border border-[var(--zv-hairline)] bg-[var(--zv-surface)]">
        <table class="w-full border-collapse text-[13px]">
          <thead class="sticky top-0 z-10 bg-[var(--zv-surface-alt)]">
            <tr class="border-b border-[var(--zv-hairline-strong)]">
              <th class="z-label px-2 py-1 text-left font-normal">{{ t('server.files.colName') }}</th>
              <th class="z-label px-2 py-1 text-left font-normal">{{ t('server.files.colType') }}</th>
              <th class="z-label px-2 py-1 text-right font-normal">{{ t('server.files.colSize') }}</th>
              <th class="z-label px-2 py-1 text-left font-normal">{{ t('server.files.colMode') }}</th>
              <th class="z-label px-2 py-1 text-left font-normal">{{ t('server.files.colMtime') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-if="currentPath !== '/'"
              class="cursor-pointer border-b border-[var(--zv-hairline)] hover:bg-[var(--zv-surface-alt)]"
              @click="goParent"
            >
              <td class="px-2 py-1" colspan="5">..</td>
            </tr>
            <tr
              v-for="entry in entries"
              :key="entry.name"
              class="cursor-pointer border-b border-[var(--zv-hairline)] hover:bg-[var(--zv-surface-alt)]"
              :class="selected?.name === entry.name ? 'bg-[var(--zv-surface-alt)]' : ''"
              @click="rowClick(entry)"
              @dblclick="rowDblClick(entry)"
            >
              <td class="px-2 py-1 text-[var(--zv-text)]">
                <span :class="entry.type === 'dir' ? 'font-medium' : ''">{{ entry.name }}</span>
                <span v-if="entry.type === 'dir'" class="ml-1 text-[var(--zv-label)]">/</span>
              </td>
              <td class="px-2 py-1 text-[12px] text-[var(--zv-text-sub)]">{{ typeLabel(entry) }}</td>
              <td class="px-2 py-1 text-right tabular-nums text-[var(--zv-text-sub)]">
                {{ fmtSize(entry.size, entry.type === 'dir') }}
              </td>
              <td class="px-2 py-1 tabular-nums text-[12px] text-[var(--zv-text-sub)]">{{ fmtMode(entry.mode) }}</td>
              <td class="px-2 py-1 tabular-nums text-[12px] text-[var(--zv-text-sub)]">{{ fmtTime(entry.mtime) }}</td>
            </tr>
            <tr v-if="!loading && entries.length === 0">
              <td class="px-2 py-3 text-center text-[12px] text-[var(--zv-label)]" colspan="5">—</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex items-center justify-between border border-[var(--zv-hairline)] bg-[var(--zv-surface)] px-2 py-1 text-[12px] text-[var(--zv-label)]">
        <span>{{ t('server.files.totalEntries', { count: total }) }}</span>
        <span>{{ t('server.files.pageInfo', { page, pages }) }}</span>
        <div class="flex gap-1">
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-0.5 text-[12px] text-[var(--zv-text)] disabled:opacity-40 hover:bg-[var(--zv-surface-alt)]"
            :disabled="page <= 1 || loading"
            @click="pageChange(page - 1)"
          >
            {{ t('server.files.pagePrev') }}
          </button>
          <button
            class="border border-[var(--zv-hairline-strong)] bg-[var(--zv-surface)] px-2 py-0.5 text-[12px] text-[var(--zv-text)] disabled:opacity-40 hover:bg-[var(--zv-surface-alt)]"
            :disabled="page >= pages || loading"
            @click="pageChange(page + 1)"
          >
            {{ t('server.files.pageNext') }}
          </button>
        </div>
      </div>
    </div>

    <ZSystemAuthDialog ref="authRef" />
  </div>
</template>
