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
const requested = process.argv.find(arg => arg.startsWith('--theme='))?.slice('--theme='.length)
const selected = requested ? themes.filter(theme => theme.id === requested) : themes
assert(selected.length > 0, `Unknown theme: ${requested}`)
const request = async (url, headers) => {
  const response = await fetch(url, { headers: { ...headers, 'User-Agent': 'Mozilla/5.0 ZVP5-Deploy' }, signal: AbortSignal.timeout(20000) })
  assert.equal(response.status, 200, String(url))
  return response
}
for (const theme of selected.filter(item => item.base !== '/')) {
  const local = readFileSync(path.join(dist, theme.base, 'index.html'), 'utf8')
  const prefix = new URL(`assets/themes/${theme.id}/`, staticBase).href
  const assets = [...local.matchAll(/(?:src|href)="([^"]*\/assets\/[^"?#]+\.(?:js|css))"/g)].map(m => m[1])
  assert(assets.length > 0, `Missing compiled assets for ${theme.id}`)
  for (const asset of assets) {
    assert(asset.startsWith(prefix), `Wrong delivery origin or path: ${asset}`)
    assert(existsSync(path.join(dist, asset.slice(staticBase.href.length))), `Missing artifact: ${asset}`)
  }
  if (!live) continue
  const htmlResponse = await request(new URL(theme.base, page), { Accept: 'text/html' })
  assert.match(htmlResponse.headers.get('content-type') || '', /text\/html/i)
  const html = await htmlResponse.text()
  const entry = assets.find(url => /\/index-[^/]+\.js$/.test(url))
  assert(entry && html.includes(entry), `Stale theme HTML: ${theme.id}`)
  const deepLink = await request(new URL(`${theme.base}me`, page), { Accept: 'text/html' })
  assert.match(deepLink.headers.get('content-type') || '', /text\/html/i)
  const deepHtml = await deepLink.text()
  assert(deepHtml.includes(entry), `Theme deep link returns another app: ${theme.id}`)
  assert(deepHtml.includes('/theme-switcher.js'), `Theme deep link lost the switcher: ${theme.id}`)
  const response = await request(entry, { Origin: page.origin })
  assert.match(response.headers.get('content-type') || '', /javascript/i)
  assert.equal(response.headers.get('access-control-allow-origin'), page.origin)
  await response.body.cancel()
}
