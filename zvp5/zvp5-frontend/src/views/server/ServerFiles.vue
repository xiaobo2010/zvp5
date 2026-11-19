<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { ElDropdown } from 'element-plus/es/components/dropdown/index.mjs'
import { ElDropdownMenu } from 'element-plus/es/components/dropdown/index.mjs'
import { ElDropdownItem } from 'element-plus/es/components/dropdown/index.mjs'
import { useMediaQuery } from '@vueuse/core'
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
import { resolveErrorMessage } from '@/utils/errorMessage'
import ZFilePreview from '@/components/server/ZFilePreview.vue'
import ZHexUnit from './components/ZHexUnit.vue'
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
  Location,
  MoreFilled
} from '@element-plus/icons-vue'

const { t } = useI18n()

const DEFAULT_PAGE_SIZE = 200
// 后端只放行这两个根,页面的快捷入口与手输校验都以此为唯一口径
// 2026-11-14 安全对齐:后端 ALLOWED_ROOTS 已收敛到 /www/zvms4(生产 systemd
// ProtectHome=true 使 /home 分支从未生效),前端快捷目录同步收敛。
const QUICK_DIRS = ['/www/zvms4']

// 竖屏判定与 App.vue / UserNav.vue 同阈值(宽高比 ≤ 6/5):
// 竖屏底部是悬浮药丸导航,左侧目录栏会让表格只剩几十像素,必须改为上下排布。
const isPortrait = useMediaQuery('(max-aspect-ratio: 6/5)')

// 宽屏才铺开完整五列并把操作列固定在右侧:
// 窄屏表格总宽装不下,固定列会像盖子一样压在修改时间上。
const wideTable = useMediaQuery('(min-width: 1280px)')

const currentPath = ref('/www/zvms4')
const pathInput = ref('/www/zvms4')
const entries = ref<FileEntry[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(DEFAULT_PAGE_SIZE)
const loading = ref(false)
// 目录读取的请求序号：只有最后一次请求的结果才允许落到页面上，
// 慢的旧响应晚回来时直接丢弃（快速切换目录时最容易触发）。
let loadSeq = 0
// 写操作（新建 / 上传 / 改名 / 改权限 / 删除）在飞时锁住入口：
// 授权框与接口往返期间按钮仍可点会重复提交，也容易让用户以为没生效。
const busy = ref(false)

const authRef = ref<InstanceType<typeof ZSystemAuthDialog>>()

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

// 窄屏把时间拆成「日期 / 时刻」两行,整串必须完整渲染,不能被截成 2026-10-2…
function fmtTimeParts(mtime: number): { date: string; time: string } {
  const date = new Date(mtime * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return {
    date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    time: `${pad(date.getHours())}:${pad(date.getMinutes())}`
  }
}

function typeLabel(entry: FileEntry): string {
  if (entry.type === 'dir') return t('server.files.typeDir')
  if (entry.type === 'file') return t('server.files.typeFile')
  if (entry.type === 'symlink') return t('server.files.typeSymlink')
  return t('server.files.typeOther')
}

async function load() {
  loading.value = true
  // 请求序号:慢的旧响应晚回来时不能覆盖新目录的内容
  // （连点面包屑 / 快速切换目录时最容易触发）。
  const seq = ++loadSeq
  try {
    const offset = (page.value - 1) * pageSize.value
    const res = await listFiles(currentPath.value, offset, pageSize.value)
    if (seq !== loadSeq) return
    const data = res.data?.data
    currentPath.value = data.path
    pathInput.value = data.path
    entries.value = data.entries
    total.value = data.total
  } catch (error) {
    if (seq !== loadSeq) return
    entries.value = []
    total.value = 0
    // 列表失败不等于动态码错：走统一解析，取不到 detail 时回落到「目录读取失败」
    ElMessage({
      message: resolveErrorMessage(error, 'server.files.loadFailed'),
      type: 'error',
      grouping: true,
      plain: true
    })
  } finally {
    if (seq === loadSeq) {
      loading.value = false
    }
  }
}

// 与后端同一判定:/www/zvms4 放行、/www/zvms40 不放行(前缀必须落在分隔符边界上)
function isAllowedPath(path: string): boolean {
  return QUICK_DIRS.some((root) => path === root || path.startsWith(`${root}/`))
}

function rootOf(path: string): string {
  return QUICK_DIRS.find((root) => path === root || path.startsWith(`${root}/`)) ?? QUICK_DIRS[0]
}

function normalizePath(raw: string): string {
  const absolute = raw.startsWith('/') ? raw : `/${raw}`
  const collapsed = absolute.replace(/\/{2,}/g, '/')
  return collapsed.length > 1 ? collapsed.replace(/\/+$/, '') : collapsed
}

async function jumpPath() {
  const input = pathInput.value.trim()
  if (input && !input.startsWith('/')) {
    ElMessage({ message: t('server.files.absolutePathOnly'), type: 'warning', grouping: true, plain: true })
    return
  }
  const target = normalizePath(input)
  if (!isAllowedPath(target)) {
    ElMessage({ message: t('server.files.rootForbidden'), type: 'warning', grouping: true, plain: true })
    pathInput.value = currentPath.value
    return
  }
  currentPath.value = target
  pathInput.value = target
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
  // 已经在根目录时父级不在白名单内,静默停在原地,不弹「越界」提示
  if (!isAllowedPath(parent)) return
  pathInput.value = parent
  void jumpPath()
}

function entryPath(name: string): string {
  return currentPath.value === '/' ? `/${name}` : `${currentPath.value}/${name}`
}

const previewVisible = ref(false)
const previewPath = ref('')
const previewName = ref('')

function openEntry(entry: FileEntry) {
  if (entry.type === 'dir' || entry.type === 'symlink') {
    pathInput.value = entryPath(entry.name)
    void jumpPath()
    return
  }
  if (entry.type === 'file') {
    // 文本文件由 ZFilePreview 内的 Z-IDE 接管(自动升全屏),图片等仍走窗口态预览
    previewPath.value = entryPath(entry.name)
    previewName.value = entry.name
    previewVisible.value = true
  }
}

// 面包屑从白名单根开始,根以上的祖先点不进去,不再渲染成死链
function crumbs(): { name: string; path: string | null }[] {
  const root = rootOf(currentPath.value)
  const parts = currentPath.value.split('/').filter(Boolean)
  const depth = root.split('/').filter(Boolean).length
  const acc: { name: string; path: string | null }[] = [{ name: root, path: root }]
  let prefix = root
  for (const part of parts.slice(depth)) {
    prefix += '/' + part
    acc.push({ name: part, path: prefix })
  }
  return acc
}

// —— 写操作:mkdir / upload / rename / chmod 先双人授权,拿码后带 totp_code 发请求 —— //
// 删除已免双人码(留痕走后端审计),见 doDelete。

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
  if (busy.value) return
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
  busy.value = true
  try {
    await makeDir(target, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.mkdirDone'))
    await load()
  } catch (error) {
    authRef.value?.fail(resolveErrorMessage(error, 'server.auth.failed'))
  } finally {
    busy.value = false
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
  if (busy.value) return
  const code = await openAuth(
    t('server.files.authSceneUpload', { name: file.name, path: currentPath.value })
  )
  if (!code) return
  busy.value = true
  try {
    await uploadFile(currentPath.value, file, code, false)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.uploadDone'))
    await load()
  } catch (error) {
    const status = axiosStatus(error)
    if (status !== 409) {
      authRef.value?.fail(resolveErrorMessage(error, 'server.auth.failed'))
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
      authRef.value?.fail(resolveErrorMessage(retryError, 'server.auth.failed'))
    }
  } finally {
    busy.value = false
  }
}

async function doRename(entry: FileEntry) {
  if (busy.value) return
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
  busy.value = true
  try {
    await renamePath(from, to, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.renameDone'))
    await load()
  } catch (error) {
    authRef.value?.fail(resolveErrorMessage(error, 'server.auth.failed'))
  } finally {
    busy.value = false
  }
}

// 删除不再走双人授权:二次确认(防误删)保留,审批改为后端审计留痕。
async function doDelete(entry: FileEntry, recursive: boolean) {
  if (busy.value && !recursive) return
  const path = entryPath(entry.name)
  busy.value = true
  try {
    await deleteEntry(path, recursive)
    ElMessage.success(t('server.files.deleteDone'))
    await load()
  } catch (error) {
    const status = axiosStatus(error)
    if (status === 409 && !recursive) {
      // 后端 delete_path:非空目录 + 非递归 → 409 "目录非空,需递归删除"
      try {
        await ElMessageBox.confirm(t('server.files.deleteRecursiveConfirm'), t('server.files.delete'), {
          type: 'warning'
        })
      } catch {
        return
      }
      busy.value = false
      await doDelete(entry, true)
      return
    }
    ElMessage.error(resolveErrorMessage(error, 'server.files.deleteFailed'))
  } finally {
    busy.value = false
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
  if (busy.value) return
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
  busy.value = true
  try {
    await chmodEntry(path, mode, code)
    authRef.value?.succeed()
    ElMessage.success(t('server.files.chmodDone'))
    await load()
  } catch (error) {
    authRef.value?.fail(resolveErrorMessage(error, 'server.auth.failed'))
  } finally {
    busy.value = false
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
  <div class="files-page phi-srv-page">
    <!-- 状态条:当前目录条目数是本页最要紧的读数 -->
    <div class="phi-srv-strip">
      <ZHexUnit
        :label="t('server.files.unitLabel')"
        :value="total"
        size="lg"
        :hint="t('server.files.unitEntries', { count: total })"
      />
      <div class="phi-srv-strip__cell">
        <div class="phi-srv-unit__body">
          <span class="phi-srv-unit__label">{{ t('server.files.unitPath') }}</span>
          <span class="phi-srv-unit__hint phi-srv-unit__hint--muted font-mono">{{
            currentPath
          }}</span>
        </div>
      </div>
    </div>

    <div class="files-layout">
      <!-- 左:快捷目录 -->
      <ElCard class="quick-card w-64 shrink-0" body-class="quick-body">
        <span class="quick-card__title phi-srv-subtitle mb-2 block">{{
          t('server.files.quickDirs')
        }}</span>
        <!-- 竖屏:256px 竖排菜单会把表格挤没,改成横向可滚动的胶囊行 -->
        <div v-if="isPortrait" class="quick-chips">
          <span class="quick-chips__label phi-srv-subtitle">{{ t('server.files.quickDirs') }}</span>
          <button
            v-for="dir in QUICK_DIRS"
            :key="dir"
            type="button"
            class="quick-chip"
            :class="{ 'is-active': dir === currentPath }"
            @click="goQuick(dir)"
          >
            <ElIcon :size="14"><FolderOpened /></ElIcon>
            <span class="font-mono">{{ dir }}</span>
          </button>
        </div>
        <ElMenu v-else class="quick-menu" :default-active="currentPath" @select="goQuick">
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

      <div class="files-main">
        <!-- 工具栏 -->
        <ElCard body-class="!px-4 !py-2.5">
          <div class="files-toolbar flex flex-wrap items-center gap-2">
            <ElButton :icon="Back" @click="goParent">{{ t('server.files.parent') }}</ElButton>
            <ElInput
              v-model="pathInput"
              class="min-w-[220px] max-w-[560px] flex-1 font-mono"
              :prefix-icon="Location"
              @keyup.enter="jumpPath"
            />
            <ElButton
              type="primary"
              plain
              :icon="Refresh"
              :loading="loading"
              :disabled="loading"
              @click="load"
            >
              {{ t('server.files.refresh') }}
            </ElButton>
            <ElButton :icon="FolderAdd" :disabled="busy" @click="doMkdir">
              {{ t('server.files.mkdir') }}
            </ElButton>
            <ElButton :icon="Upload" :disabled="busy" @click="pickUploadFile">
              {{ t('server.files.upload') }}
            </ElButton>
            <span class="files-toolbar__hint phi-srv-dim">{{ t('server.files.uploadLimit') }}</span>
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
            <span class="phi-srv-subtitle tabular-nums">{{
              t('server.files.totalEntries', { count: total })
            }}</span>
          </div>

          <!-- 加载遮罩只包住表格内容:宿主是一个只装表格的独立盒子,
               工具栏与底部分页在盒子外面,加载时也照常可点。 -->
          <div v-loading="loading" class="files-table-wrap min-h-0 flex-1">
            <ElTable
              :data="entries"
              row-key="name"
              stripe
              height="100%"
              class="files-table"
            >
              <ElTableColumn
                prop="name"
                :label="t('server.files.colName')"
                :min-width="wideTable ? 220 : 140"
                show-overflow-tooltip
              >
                <template #default="{ row }">
                  <span
                    class="name-cell"
                    role="button"
                    tabindex="0"
                    @click="openEntry(row)"
                    @keyup.enter="openEntry(row)"
                  >
                    {{ row.name }}<span v-if="row.type === 'dir'" class="text-[var(--yg-text-tertiary)]">/</span>
                  </span>
                  <!-- 窄屏:大小与类型折进名称单元格的副行,给修改时间腾出位置 -->
                  <span v-if="!wideTable" class="name-cell__meta">
                    {{ fmtSize(row.size, row.type === 'dir') }} · {{ typeLabel(row) }}
                  </span>
                </template>
              </ElTableColumn>
              <ElTableColumn
                v-if="wideTable"
                prop="size"
                :label="t('server.files.colSize')"
                width="100"
              >
                <template #default="{ row }">
                  <span class="tabular-nums text-[var(--yg-text-secondary)]">{{ fmtSize(row.size, row.type === 'dir') }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn
                prop="mtime"
                :label="t('server.files.colMtime')"
                :width="wideTable ? 150 : 112"
              >
                <template #default="{ row }">
                  <span
                    v-if="wideTable"
                    class="tabular-nums text-[var(--yg-text-secondary)]"
                  >{{ fmtTime(row.mtime) }}</span>
                  <span v-else class="mtime-cell">
                    <span>{{ fmtTimeParts(row.mtime).date }}</span>
                    <span>{{ fmtTimeParts(row.mtime).time }}</span>
                  </span>
                </template>
              </ElTableColumn>
              <ElTableColumn
                v-if="wideTable"
                prop="type"
                :label="t('server.files.colType')"
                width="90"
              >
                <template #default="{ row }">
                  <span class="text-[var(--yg-text-secondary)]">{{ typeLabel(row) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn
                :label="t('server.files.colActions')"
                :width="wideTable ? 320 : 124"
                align="right"
                :fixed="wideTable ? 'right' : false"
              >
                <template #default="{ row }">
                  <!-- 宽屏:平铺文字按钮,整行操作一眼看全 -->
                  <div v-if="wideTable" class="row-actions">
                    <ElButton
                      v-if="row.type === 'file'"
                      text
                      bg
                      size="small"
                      :icon="Download"
                      :disabled="busy"
                      @click="doDownload(row as FileEntry)"
                    >
                      {{ t('server.files.download') }}
                    </ElButton>
                    <ElButton
                      text
                      bg
                      size="small"
                      :icon="EditPen"
                      :disabled="busy"
                      @click="doRename(row as FileEntry)"
                    >
                      {{ t('server.files.rename') }}
                    </ElButton>
                    <ElButton
                      text
                      bg
                      size="small"
                      :icon="Key"
                      :disabled="busy"
                      @click="doChmod(row as FileEntry)"
                    >
                      {{ t('server.files.chmod') }}
                    </ElButton>
                    <ElButton
                      text
                      bg
                      size="small"
                      type="danger"
                      :icon="Delete"
                      :disabled="busy"
                      @click="onDeleteClick(row as FileEntry)"
                    >
                      {{ t('server.files.delete') }}
                    </ElButton>
                  </div>
                  <!-- 窄屏:收成图标按钮 +「更多」下拉,列宽压到 124px 以内 -->
                  <div v-else class="row-actions row-actions--compact">
                    <ElButton
                      v-if="row.type === 'file'"
                      class="act-btn"
                      circle
                      :icon="Download"
                      :disabled="busy"
                      :aria-label="t('server.files.download')"
                      :title="t('server.files.download')"
                      @click="doDownload(row as FileEntry)"
                    />
                    <ElDropdown trigger="click">
                      <ElButton
                        class="act-btn"
                        circle
                        :icon="MoreFilled"
                        :disabled="busy"
                        :aria-label="t('server.files.colActions')"
                        :title="t('server.files.colActions')"
                      />
                      <template #dropdown>
                        <ElDropdownMenu>
                          <ElDropdownItem
                            v-if="row.type === 'file'"
                            :icon="Download"
                            @click="doDownload(row as FileEntry)"
                          >
                            {{ t('server.files.download') }}
                          </ElDropdownItem>
                          <ElDropdownItem :icon="EditPen" @click="doRename(row as FileEntry)">
                            {{ t('server.files.rename') }}
                          </ElDropdownItem>
                          <ElDropdownItem :icon="Key" @click="doChmod(row as FileEntry)">
                            {{ t('server.files.chmod') }}
                          </ElDropdownItem>
                          <ElDropdownItem
                            divided
                            :icon="Delete"
                            @click="onDeleteClick(row as FileEntry)"
                          >
                            {{ t('server.files.delete') }}
                          </ElDropdownItem>
                        </ElDropdownMenu>
                      </template>
                    </ElDropdown>
                  </div>
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
    <ZFilePreview v-model="previewVisible" :path="previewPath" :name="previewName" />
  </div>
</template>

<style scoped>
/* ---------- 页面骨架 ---------- */
.files-page {
  flex: 1 1 auto;
  min-height: 0;
}

/* 横屏沿用既有高度算法,桌面观感不变。
   顶部新增了状态条（六边形徽章约 86px + 上下留白 + 间距），
   扣减量相应加大，否则表格区会顶出视口。 */
.files-layout {
  display: flex;
  align-items: stretch;
  gap: var(--yg-space-4);
  height: calc(100vh - 430px);
  min-height: 480px;
}
.files-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  gap: var(--yg-space-4);
}

/* 竖屏:底部是悬浮药丸导航,高度交给 flex 分配;
   左侧目录栏改为横向胶囊行,表格才有可用宽度。 */
@media (max-aspect-ratio: 6/5) {
  .files-layout {
    flex-direction: column;
    height: auto;
    min-height: 0;
    flex: 1 1 auto;
    gap: var(--yg-space-3);
    /* 兜底:即便容器高度算错,也不许高过「视口减去底部让位」,
       保证表格底边永远在底部悬浮药丸上方。
       底部让位直接引用外壳的 --yg-content-bottom-inset(App.vue 在
       .yg-layout__content 上下发,随 DOM 继承),本页不再写第二份数值。
       视口基准跟外壳一致用 100vh,不混用 100dvh,免得两套基准打架。 */
    max-height: calc(100vh - var(--yg-content-bottom-inset, 96px));
  }

  .files-main {
    gap: var(--yg-space-3);
    min-height: 0;
  }

  .quick-card {
    width: auto;
  }

  .quick-card__title {
    display: none;
  }

  /* 竖屏:胶囊行贴着卡片内边距走,别让卡片吃掉一屏高度 */
  .quick-card :deep(.el-card__body) {
    padding: var(--yg-space-2);
  }

  .files-toolbar {
    gap: var(--yg-space-1);
  }

  /* 竖屏:路径输入框让出宽度,工具栏排成两行而不是三行 */
  .files-toolbar :deep(.el-input) {
    min-width: 140px;
  }

  .files-toolbar__hint {
    display: none;
  }
}

.quick-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

/* 竖屏快捷目录:一行可横向滚动的胶囊 */
.quick-chips {
  display: flex;
  align-items: center;
  gap: var(--yg-space-2);
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
  padding-bottom: 2px;
}

.quick-chips::-webkit-scrollbar {
  display: none;
}

.quick-chips__label {
  flex: 0 0 auto;
  white-space: nowrap;
}

.quick-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--yg-space-1);
  flex: 0 0 auto;
  min-height: 44px;
  padding: 0 var(--yg-space-3);
  border: 1px solid var(--yg-border-default);
  border-radius: var(--yg-radius-pill);
  background: var(--yg-bg-surface);
  color: var(--yg-text-secondary);
  font-size: var(--yg-font-size-sm);
  cursor: pointer;
  white-space: nowrap;
  transition:
    color var(--yg-dur-fast) var(--yg-ease-out),
    background-color var(--yg-dur-fast) var(--yg-ease-out),
    border-color var(--yg-dur-fast) var(--yg-ease-out);
}

.quick-chip:hover {
  background: var(--yg-bg-hover);
  color: var(--yg-text-primary);
}

.quick-chip.is-active {
  border-color: var(--yg-color-primary);
  background: var(--yg-color-primary-soft);
  color: var(--yg-color-primary);
  font-weight: var(--yg-font-weight-medium);
}

.quick-chip:focus-visible {
  outline: none;
  box-shadow: var(--yg-focus-ring);
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
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 0;
  background: transparent;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.name-cell:hover {
  color: var(--yg-color-primary);
  text-decoration: underline;
}

/* 窄屏:名称副行(大小 · 类型) */
.name-cell__meta {
  display: block;
  font-size: var(--yg-font-size-xs);
  color: var(--yg-text-tertiary);
}

/* 窄屏:修改时间拆成日期 / 时刻两行,整串完整渲染 */
.mtime-cell {
  display: inline-flex;
  flex-direction: column;
  font-size: var(--yg-font-size-sm);
  font-variant-numeric: tabular-nums;
  line-height: 1.35;
  color: var(--yg-text-secondary);
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

/* 表格区加载宿主:EP 的 v-loading 遮罩是 position:absolute + inset:0,
   这里把宿主钉成 relative 并裁掉溢出,遮罩严格限制在表格区内,
   绝不越界盖住上面的面包屑或下面的分页按钮。 */
.files-table-wrap {
  position: relative;
  overflow: hidden;
}

/* 行内操作:宽屏平铺文字按钮,窄屏收成图标 +「更多」 */
.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--yg-space-1);
}

.row-actions--compact {
  gap: var(--yg-space-2);
}

/* 触控目标 44×44(准则 3.7) */
.files-table :deep(.act-btn.el-button) {
  width: 44px;
  height: 44px;
  min-height: 44px;
  padding: 0;
  margin: 0;
  border-radius: var(--yg-radius-pill);
}
</style>
