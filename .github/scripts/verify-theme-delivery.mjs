// Run after assembly and again after activation (inside the deployment rollback trap).
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

const dist = 'zvp5/zvp5-frontend/dist'
const themes = JSON.parse(readFileSync('zvp5/themes/manifest.json', 'utf8')).themes
const staticBase = new URL(process.env.VITE_STATIC_BASE)
const page = new URL(process.env.VITE_PAGE_BASE)
assert.notEqual(page.origin, staticBase.origin, 'Production requires a separate static origin. Check PROD_STATIC_BASE.')
const live = process.argv.includes('--live')
const request = async (url, headers) => {
  const response = await fetch(url, { headers: { ...headers, 'User-Agent': 'Mozilla/5.0 ZVP5-Deploy' }, signal: AbortSignal.timeout(20000) })
  assert.equal(response.status, 200, String(url))
  return response
}
for (const theme of themes.filter(item => item.base !== '/')) {
  const local = readFileSync(path.join(dist, theme.base, 'index.html'), 'utf8')
  const prefix = new URL(`assets/themes/${theme.id}/`, staticBase).href
  const assets = [...local.matchAll(/(?:src|href)="([^"]*\/assets\/[^"?#]+\.(?:js|css))"/g)].map(m => m[1])
  assert(assets.length > 0, `Missing compiled assets for ${theme.id}`)
  for (const asset of assets) {
    assert(asset.startsWith(prefix), `Wrong delivery origin or path: ${asset}`)
    assert(existsSync(path.join(dist, asset.slice(staticBase.href.length))), `Missing artifact: ${asset}`)
  }
  if (!live) continue
  const html = await (await request(new URL(theme.base, page), { Accept: 'text/html' })).text()
  const entry = assets.find(url => /\/index-[^/]+\.js$/.test(url))
  assert(entry && html.includes(entry), `Stale theme HTML: ${theme.id}`)
  const response = await request(entry, { Origin: page.origin })
  assert.match(response.headers.get('content-type') || '', /javascript/i)
  assert.equal(response.headers.get('access-control-allow-origin'), page.origin)
  await response.body.cancel()
}
