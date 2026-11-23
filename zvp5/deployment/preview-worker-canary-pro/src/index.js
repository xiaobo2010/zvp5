// canary-pro 静态资源 Worker（前端产物来自 zvp5/zvp5-frontend-pro）。
//
// 与 canary 的 preview-worker 同一套逻辑：强制 https，之后原样交给
// ASSETS 绑定。必须强制的原因是微信/华为平板等内置浏览器允许直接用 http
// 打开预览域，页面本身能加载（Worker 不跳转），但页面里的 API 基址是 https
// 绝对地址，跨域预检携带的 Origin 会变成 `http://...`，而后端 CORS 白名单
// 只放行 https 版本，Starlette 直接回 400 `Disallowed CORS origin`。
export default {
  async fetch(request, env) {
    const url = new URL(request.url)

    if (url.protocol === 'http:') {
      url.protocol = 'https:'
      return Response.redirect(url.toString(), 301)
    }

    const response = await env.ASSETS.fetch(request)
    const headers = new Headers(response.headers)
    // 让后续访问直接在浏览器层升级到 https，不再回到这个分支。
    headers.set('Strict-Transport-Security', 'max-age=31536000')

    // HTML 入口不能进 Cloudflare 边缘缓存：预览域没有 nginx 的 no-cache
    // 规则，旧 HTML 会继续引用旧 hash 的 assets，部署成功了用户却还看旧版。
    // 静态 assets 仍然长缓存。
    const isHtmlEntry =
      request.method === 'GET' &&
      (url.pathname === '/' ||
        url.pathname.endsWith('/') ||
        url.pathname.endsWith('.html'))
    if (isHtmlEntry) {
      headers.set('Cache-Control', 'no-store, max-age=0')
      headers.set('CDN-Cache-Control', 'no-store')
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers
    })
  }
}
