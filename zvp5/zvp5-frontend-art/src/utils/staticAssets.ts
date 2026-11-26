/** Prefix a file under `public/` with the build-time static asset base. */
export function staticAssetUrl(path: string): string {
  const base = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '')
  const asset = path.replace(/^\/+/, '')
  return `${base}/${asset}`
}
