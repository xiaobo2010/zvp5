// Role helpers for the standalone Pro app.
//
// The backend puts the permission list in the JWT `per` claim. Keep this
// dependency-free and cache the decoded payload so route guards and the drawer
// do not re-decode the same token on every navigation.

let cachedToken = ''
let cachedRoles: string[] | null = null

function decodeRoles(token: string): string[] {
  if (token === cachedToken && cachedRoles) return cachedRoles

  cachedToken = token
  cachedRoles = []
  if (!token) return cachedRoles

  try {
    const encoded = token.split('.')[1]
    if (!encoded) return cachedRoles
    const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
    const json = decodeURIComponent(
      Array.from(atob(padded))
        .map((char) => '%' + char.charCodeAt(0).toString(16).padStart(2, '0'))
        .join('')
    )
    const payload = JSON.parse(json) as { per?: unknown; roles?: unknown }
    const raw = Array.isArray(payload.per)
      ? payload.per
      : Array.isArray(payload.roles)
        ? payload.roles
        : []
    cachedRoles = Array.from(
      new Set(raw.filter((role): role is string => typeof role === 'string' && Boolean(role)))
    )
  } catch {
    cachedRoles = []
  }

  return cachedRoles
}

export function currentRoles(): string[] {
  return decodeRoles(localStorage.getItem('token') ?? '')
}

export function hasAnyRole(...roles: string[]): boolean {
  if (roles.length === 0) return true
  const current = currentRoles()
  return roles.some((role) => current.includes(role))
}

export function isAdmin(): boolean {
  return currentRoles().includes('admin')
}

export function isAdminOrDepartment(): boolean {
  const roles = currentRoles()
  return roles.includes('admin') || roles.includes('department')
}
