# 静态资源拆域到 zvpapi（2026-11-12）

## 目标

页面壳继续由 `https://zvp.xiaobopisces.top` 提供，JS、CSS、字体、图片、PWA 图标及
Workbox 预缓存改由高带宽的 `https://zvpapi.xiaobopisces.top` 提供。

## 构建

```bash
VITE_API_BASE=/api \
VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api \
VITE_STATIC_BASE=https://zvpapi.xiaobopisces.top/ \
VITE_PAGE_BASE=https://zvp.xiaobopisces.top/ \
pnpm build
```

`VITE_STATIC_BASE` 必须是 `/` 或带结尾斜杠的绝对 HTTPS URL。未设置时保持原有
同源行为，便于本地开发和回滚。

构建后应满足：

- `index.html` 中所有 `/assets/*`、favicon、Apple touch icon、mask icon 均为
  `https://zvpapi.xiaobopisces.top/*`；
- `manifest.webmanifest` 从 `zvpapi` 读取，图标也位于 `zvpapi`，但
  `start_url`/`scope` 仍锚定 `VITE_PAGE_BASE`；
- `sw.js` 的 Workbox 预缓存条目位于 `zvpapi`。

## 部署

1. 将同一份 `dist/` 部署到 `zvp` 和 `zvpapi` 两个 vhost 的
   `/www/zvms4/frontend-dist`。
2. `zvpapi` 的 `/assets/`、`/index.html`、`/apple-touch-icon.png`、`/favicon.ico`
   和 `/manifest.webmanifest` 必须对 `https://zvp.xiaobopisces.top` 返回
   `Access-Control-Allow-Origin`；Workbox 使用带凭据的跨域预缓存，因此还必须
   返回 `Access-Control-Allow-Credentials: true`。
3. `zvp` 的 CSP 必须允许 `zvpapi` 作为 `script-src`、`style-src`、`img-src`、
   `font-src`、`connect-src` 和 `manifest-src`。
4. 清理旧 Service Worker 缓存并刷新页面验收。

## Service Worker 例外

浏览器规范只允许页面从同源注册 Service Worker，因此 `/sw.js` 的注册文件本身仍由
`zvp` 提供；这是唯一无法迁到 `zvpapi` 的静态请求。Service Worker 内部预缓存和
运行时代码均已指向 `zvpapi`。跨域 Manifest 需要 `zvpapi` 返回
`Access-Control-Allow-Origin: https://zvp.xiaobopisces.top` 和
`Access-Control-Allow-Credentials: true`，并由 CSP 的 `manifest-src` 放行。
Workbox 预缓存 `index.html`、根目录 PWA 图标和 `/assets/*` 时同样使用带凭据的
跨域请求，因此这些响应也必须返回同样的 CORS 头，否则 Service Worker 安装会因
`Access-Control-Allow-Origin` 缺失而失败。
