/**
 * @description Trigger a browser download for an in-memory blob payload.
 */
export function downloadBlob(data: BlobPart, mime: string, filename: string): void {
  const file = new Blob([data], { type: mime })
  const url = URL.createObjectURL(file)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
