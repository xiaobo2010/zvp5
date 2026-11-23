// Public server clock for automatic palette selection.
//
// `GET /v1/themes/server-time` is intentionally unauthenticated (the login
// page uses the same phase), so this module uses a plain `fetch` instead of the
// shared `apiRequest` helper: an expired token must never trigger the helper's
// 401 logout path just to read the hour.

import { API_BASE } from './http'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function readHour(payload: unknown): number | null {
  if (!isRecord(payload) || payload.status !== 'ok' || !isRecord(payload.data)) return null
  const hour = payload.data.hour
  return typeof hour === 'number' && Number.isInteger(hour) && hour >= 0 && hour <= 23
    ? hour
    : null
}

/**
 * Read the server's wall-clock hour (0–23).
 *
 * Returns `null` on any failure so the caller can keep its local-clock
 * fallback. The response's `now_iso` is deliberately ignored: reinterpreting it
 * through the browser timezone is exactly the tablet bug this replaces.
 */
export async function fetchServerHour(signal: AbortSignal): Promise<number | null> {
  const response = await fetch(`${API_BASE}/v1/themes/server-time`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'error',
    signal,
  })
  if (!response.ok) return null
  try {
    return readHour(await response.json())
  } catch {
    return null
  }
}
