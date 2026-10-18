import axios from '@/plugins/axios.ts'

// 与后端 services/file_ops.py:_describe 字段一一对应
export interface FileEntry {
  name: string
  type: 'dir' | 'file' | 'symlink' | 'other'
  size: number
  mode: number
  mtime: number
  is_symlink: boolean
  target?: string | null
}

export interface FileListData {
  path: string
  offset: number
  limit: number
  total: number
  entries: FileEntry[]
}

export interface FileListResponse {
  status: string
  code: number
  data: FileListData
}

export interface FileOpResponse {
  status: string
  code: number
  data: Record<string, unknown>
}

// 读操作不过双人码,但同样受黑名单约束(403)
export function listFiles(path: string, offset = 0, limit = 500) {
  return axios.get<FileListResponse>('/system/files/list', {
    params: { path, offset, limit }
  })
}

export function statFile(path: string) {
  return axios.get<FileOpResponse>('/system/files/stat', {
    params: { path }
  })
}

// 流式下载,≤256MB;失败时后端 detail 会给出中文原因(413/403/400)
export function downloadFile(path: string) {
  return axios.get<Blob>('/system/files/download', {
    params: { path },
    responseType: 'blob'
  })
}

// 以下写操作请求体一律带 totp_code;字段名以后端 routers/system_files_router.py 为准
export function makeDir(path: string, totpCode: string) {
  return axios.post<FileOpResponse>('/system/files/mkdir', {
    path,
    totp_code: totpCode
  })
}

export function uploadFile(path: string, file: File, totpCode: string, overwrite = false) {
  const form = new FormData()
  form.append('path', path)
  form.append('file', file)
  form.append('overwrite', String(overwrite))
  form.append('totp_code', totpCode)
  return axios.post<FileOpResponse>('/system/files/upload', form)
}

// 后端 RenameRequest 用 alias "from"/"to"(from 是 Python 关键字)
export function renamePath(from: string, to: string, totpCode: string) {
  return axios.post<FileOpResponse>('/system/files/rename', {
    from,
    to,
    totp_code: totpCode
  })
}

export function deleteEntry(path: string, recursive: boolean, totpCode: string) {
  return axios.post<FileOpResponse>('/system/files/delete', {
    path,
    recursive,
    totp_code: totpCode
  })
}

// mode 必须是整数(后端 Field(strict=True)),调用方用 parseInt(x, 8) 转八进制
export function chmodEntry(path: string, mode: number, totpCode: string) {
  return axios.post<FileOpResponse>('/system/files/chmod', {
    path,
    mode,
    totp_code: totpCode
  })
}
