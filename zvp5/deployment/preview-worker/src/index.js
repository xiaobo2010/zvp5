// 预览站静态资源 Worker。
//
// 必须强制 https：微信/华为平板等内置浏览器允许用户直接用 http 打开
// canary.xiaobopisces.top，页面本身能加载（Worker 不跳转），但页面里的 API
// 基址是 https 绝对地址，跨域预检携带的 Origin 会变成 `http://canary...`，
// 而后端 ZVMS_CORS_ALLOWED_ORIGINS 只放行 https 版本，Starlette 直接回
// 400 `Disallowed CORS origin`，前端兜底提示「网络错误」。
// 2026-11-11 线上实测：http 页面来的 OPTIONS /api/cert 全部 400。
//
// 同源多主题：除根 canary 外，其余主题都是**各自独立的完整前端构建**，产物由
// `zvp5/deployment/build-theme-apps.mjs` 按 `zvp5/themes/manifest.json` 拷到
// `dist/themes/<id>/`。它们各自是独立 SPA，深链接（如 `/themes/canary-pro/me`）
// 必须回落到自己的 index.html，不能落到根 canary 的 index.html——否则会加载错应用。
// 这里不再维护主题 id 列表：任何 `/themes/<slug>` 都按子应用处理，清单仍是
// 唯一事实源（新增主题不需要改 Worker）。
const THEME_PREFIX = '/themes/'
const THEME_ID_RE = /^[a-z0-9][a-z0-9-]*$/

/** 该请求是否是浏览器地址栏导航（要的是 HTML 页面，不是资源）。 */
function isHtmlNavigation(request, url) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return false
  }
  const accept = request.headers.get('Accept') || ''
  if (!accept.includes('text/html')) {
    return false
  }
  // 带扩展名的路径（/assets/x.js、/favicon.ico）一律按静态资源处理。
  const lastSegment = url.pathname.slice(url.pathname.lastIndexOf('/') + 1)
  return !lastSegment.includes('.')
}

/** 命中 `/<prefix><id>/...` 时返回该子应用的 index.html 地址，否则 null。 */
function themeAppIndexUrl(url) {
  if (!url.pathname.startsWith(THEME_PREFIX)) {
    return null
  }
  const id = url.pathname.slice(THEME_PREFIX.length).split('/')[0]
  if (!THEME_ID_RE.test(id)) {
    return null
  }
  return new URL(`${THEME_PREFIX}${id}/index.html`, url.origin)
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url)

    if (url.protocol === 'http:') {
      url.protocol = 'https:'
      return Response.redirect(url.toString(), 301)
    }

    // 子应用的 SPA 深链接：只对 HTML 导航改写到自己的 index.html，
    // 静态资源（/themes/canary2/assets/*.js 等）仍然按原路径取。
    const themeIndex = themeAppIndexUrl(url)
    const asset =
      themeIndex && isHtmlNavigation(request, url) ? themeIndex.toString() : request

    const response = await env.ASSETS.fetch(asset)
    const headers = new Headers(response.headers)
    // 让后续访问直接在浏览器层升级到 https，不再回到这个分支。
    headers.set('Strict-Transport-Security', 'max-age=31536000')
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers
    })
  }
}
