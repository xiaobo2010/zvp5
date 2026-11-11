// 预览站静态资源 Worker。
//
// 必须强制 https：微信/华为平板等内置浏览器允许用户直接用 http 打开
// canary.xiaobopisces.top，页面本身能加载（Worker 不跳转），但页面里的 API
// 基址是 https 绝对地址，跨域预检携带的 Origin 会变成 `http://canary...`，
// 而后端 ZVMS_CORS_ALLOWED_ORIGINS 只放行 https 版本，Starlette 直接回
// 400 `Disallowed CORS origin`，前端兜底提示「网络错误」。
// 2026-11-11 线上实测：http 页面来的 OPTIONS /api/cert 全部 400。
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
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers
    })
  }
}
