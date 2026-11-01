import type { Dayjs } from 'dayjs'
import dayjs from 'dayjs'
import { axios } from '@/plugins'
import { downloadBlob } from '@/utils/download'

type ExportFormat = 'csv' | 'excel' | 'json' | 'html' | 'latex'

function extensionOf(format: ExportFormat) {
  return format === 'excel' ? 'xlsx' : format === 'latex' ? 'tex' : format
}

/**
 * 下载文件名：name 为空（users/activities 型导出没有名称输入框）时兜底成
 * `export-YYYYMMDD-HHmmss`，避免浏览器只拿到一个扩展名。
 */
export function exportFileName(name: string | undefined | null, format: ExportFormat) {
  const base = name?.trim() || `export-${dayjs().format('YYYYMMDD-HHmmss')}`
  return `${base}.${extensionOf(format)}`
}

async function createExportTask(
  type: 'time' | 'users' | 'activities',
  format: 'csv' | 'json' | 'excel' | 'html' | 'latex',
  start?: Dayjs,
  end?: Dayjs,
  allowCache: boolean = false,
  includeDescription: boolean = false // Default to false, can be changed in the UI
) {
  const body = {
    format,
    start: start?.toISOString(),
    end: end?.toISOString(),
    allow_cache: allowCache,
    include_description: includeDescription
  }
  const result = await axios(`/exports/${type}`, {
    data: body,
    method: 'post'
  })
  if (result.data) {
    return result.data.data
  }
}

async function queryTaskStatus(id: string) {
  const result = await axios(`/exports/${id}`, {
    method: 'get'
  })
  return result.data.data as {
    status: 'pending' | 'processing' | 'completed' | 'failed'
    percentage: number
  }
}

async function downloadTaskFile(
  id: string,
  name: string,
  format: ExportFormat,
  language: string = 'en-US' // Default to English, can be changed in the UI
) {
  const result = await axios(`/exports/${id}/file`, {
    method: 'get',
    params: { language },
    responseType: 'blob'
  })
  const mime = (
    {
      csv: 'text/csv',
      excel: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      json: 'application/json',
      html: 'text/html',
      latex: 'application/octet-stream'
    } as { [key: string]: string }
  )[format]
  downloadBlob(result.data, mime, exportFileName(name, format))
}

export { createExportTask as create, queryTaskStatus as query, downloadTaskFile as download }
