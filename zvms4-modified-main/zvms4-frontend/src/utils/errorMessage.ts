/**
 * 把任意错误对象压成一句能直接展示的文案。
 * 后端两种外壳都覆盖：`{"detail": "..."}` 与 `{"message": "..."}`。
 */
export function errorMessage(error: unknown, fallback = '操作失败，请稍后重试'): string {
  const anyError = error as
    | { response?: { data?: { detail?: unknown; message?: unknown } }; message?: string }
    | undefined
  const data = anyError?.response?.data
  const raw = data?.detail ?? data?.message ?? anyError?.message
  if (typeof raw === 'string' && raw.trim()) {
    return raw
  }
  if (Array.isArray(raw) && raw.length) {
    return raw.map((item) => String((item as { msg?: string })?.msg ?? item)).join('；')
  }
  return fallback
}

/**
 * 取错误文案，取不到就用 fallback（主分支的同名工具签名）。
 */
export function resolveErrorMessageOr(error: unknown, fallback: string): string {
  return errorMessage(error, fallback)
}
