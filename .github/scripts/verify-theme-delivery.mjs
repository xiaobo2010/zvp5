import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

const dist = 'zvp5/zvp5-frontend/dist'
const themes = JSON.parse(readFileSync('zvp5/themes/manifest.json', 'utf8')).themes
const staticBase = new URL(process.env.VITE_STATIC_BASE)
const page = new URL(process.env.VITE_PAGE_BASE)
assert.notEqual(page.origin, staticBase.origin, 'Production requires a separate static origin. Check PROD_STATIC_BASE.')
const live = process.argv.includes('--live')

const request = async (url, headers) => {
  const response = await fetch(url, {
    headers: { ...headers, 'User-Agent': 'Mozilla/5.0 ZVP5-Deploy' },
    signal: AbortSignal.timeout(20000)
  })
  assert.equal(response.status, 200, String(url))
  return response
}

function filesUnder(root) {
  if (!existsSync(root)) return []
  const files = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const absolute = path.join(root, entry.name)
    if (entry.isDirectory()) files.push(...filesUnder(absolute))
    else files.push(absolute)
  }
  return files
}

for (const theme of themes.filter((item) => item.base !== '/')) {
  const local = readFileSync(path.join(dist, theme.base, 'index.html'), 'utf8')
  const prefix = new URL(`assets/themes/${theme.id}/`, staticBase).href
  // Check every emitted asset reference, not only JS/CSS. A stale theme build
  // used to leave artwork URLs on the page origin, where nginx returned text/plain.
  const assets = [
    ...local.matchAll(/(?:src|href)="([^"?#]*\/assets\/[^"?#]+)"/g)
  ].map((match) => match[1])
  assert(assets.length > 0, `Missing compiled assets for ${theme.id}`)
  for (const asset of assets) {
    assert(asset.startsWith(prefix), `Wrong delivery origin or path: ${asset}`)
    assert(existsSync(path.join(dist, asset.slice(staticBase.href.length))), `Missing artifact: ${asset}`)
  }

  if (!live) continue

  const pageResponse = await request(new URL(theme.base, page), { Accept: 'text/html' })
  const html = await pageResponse.text()
  const cacheControl = pageResponse.headers.get('cache-control') || ''
  assert.match(
    cacheControl,
    /(?:^|,)\s*(?:no-store|no-cache)(?:\s*(?:,|$))/i,
    `Theme HTML must not be cached between releases: ${new URL(theme.base, page)} ` +
      `returned Cache-Control: ${cacheControl || '(missing)'}`
  )

  const entry = assets.find((url) => /\/index-[^/]+\.js$/.test(url))
  assert(entry && html.includes(entry), `Stale theme HTML: ${theme.id}`)
  const response = await request(entry, { Origin: page.origin })
  assert.match(response.headers.get('content-type') || '', /javascript/i)
  assert.equal(response.headers.get('access-control-allow-origin'), page.origin)
  await response.body.cancel()

  // Exercise one real raster asset when the theme emits one. This catches the
  // old page-origin text/plain fallback that made the canvas report an image
  // load failure even though the entry script itself was healthy.
  const image = filesUnder(path.join(dist, 'assets', 'themes', theme.id, 'assets')).find((file) =>
    /\.(?:avif|gif|jpe?g|png|webp)$/i.test(file)
  )
  if (image) {
    const relative = path.relative(dist, image).split(path.sep).join('/')
    const imageResponse = await request(new URL(relative, staticBase), { Origin: page.origin })
    assert.match(imageResponse.headers.get('content-type') || '', /^image\//i)
    assert.equal(imageResponse.headers.get('access-control-allow-origin'), page.origin)
    await imageResponse.body.cancel()
  }
}
