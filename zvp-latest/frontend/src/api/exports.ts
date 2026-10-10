import axios from '@/plugins/axios'

export async function exportUsers() {
  return (
    (await axios('/exports/users', { method: 'post' })).data as { data: string }
  ).data
}

export async function exportTime(properties: Record<string, unknown> = {}) {
  return (
    (
      await axios('/exports/time', {
        method: 'post',
        data: properties
      })
    ).data as { data: string }
  ).data
}

export async function getExport(taskId: string) {
  return (await axios(`/exports/${taskId}`)).data as { status: string }
}

export async function getExportFile(taskId: string) {
  return (await axios(`/exports/${taskId}/file`, { responseType: 'blob' })).data as Blob
}

export async function getGroupTemplate(gid: string) {
  return (
    await axios(`/groups/${gid}/template`, {
      params: { export_format: 'excel' },
      responseType: 'blob'
    })
  ).data as Blob
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export async function importActivityExcel(name: string, desc: string, file: File) {
  const form = new FormData()
  form.append('name', name)
  form.append('desc', desc)
  form.append('payload', file)
  await axios('/imports/activities', {
    method: 'post',
    params: { name, desc },
    data: form
  })
}

export async function requestReports() {
  return (await axios('/exports/reports', { method: 'post' })).data as string
}

export async function downloadReports(token: string) {
  return (
    await axios('/exports/reports/download', {
      params: { token },
      responseType: 'blob'
    })
  ).data as Blob
}
