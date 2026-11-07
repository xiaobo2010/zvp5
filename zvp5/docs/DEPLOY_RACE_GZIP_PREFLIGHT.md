# 双路竞速扩展、API gzip 与预检部署要求

> 状态：已于 2026-11-07 完成首尔、洛杉矶和后端前端发布。发布记录见文末。

## 1. 发布物

仓库内对应文件：

```text
zvms4-frontend/src/plugins/hedgedGet.ts
zvms4-frontend/src/plugins/hedgedGet.spec.ts
deployment/nginx/zvms-cors-map.conf
deployment/nginx/zvms-api-preflight.conf
deployment/nginx/zvms-api-gzip.conf
```

前端必须使用双基址构建：

```bash
cd zvms4-modified-main/zvms4-frontend
VITE_API_BASE=https://zvp.xiaobopisces.top/api \
VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api \
pnpm build
```

构建后必须同时命中：

```bash
rg -l 'https://zvp.xiaobopisces.top/api' dist/assets
rg -l 'https://zvpapi.xiaobopisces.top/api' dist/assets
rg -l 'Both API paths failed' dist/assets
```

## 2. 竞速范围

允许竞速的请求必须同时满足：方法为 GET、默认 JSON/text 响应、命中白名单、未设置 Range、
不是 SSE、不是内容站本机接口。

白名单覆盖实际访问日志中的常用 JSON GET：

- 版本、健康、changelog、证书、metadata/session、日志；
- 通知、维护状态、角色策略；
- 活动、用户、分组列表/详情及分页子资源、活动统计；
- 设备状态、封禁规则/触发/用户/设备/违规；
- 附件 JSON 列表及 meta/audit/ocr；
- AI 会话列表与详情、AI runs、Agent proposals、OpenAI 模型列表。

明确保持单路：

```text
GET /ai/conversations/{id}/messages
GET /attachments/{id}/thumb
GET /attachments/{id}
文件下载、导出文件、上传、所有写请求、SSE、Range 和流式响应
```

竞速结果为完整响应竞速。AI 页面使用 `validateStatus: () => true`，因此竞速层必须只让
2xx 获胜；非 2xx 要等待另一条线路。两路都不是 2xx 时才返回主线路原始响应或错误。

## 3. 首尔 Nginx 要求

目标主机：`43.128.154.199`（tencent-43 / zvpapi）。

安装：

```bash
install -m 0644 deployment/nginx/zvms-cors-map.conf \
  /etc/nginx/conf.d/zvms-cors-map.conf
install -m 0644 deployment/nginx/zvms-api-gzip.conf \
  /etc/nginx/conf.d/zvms-api-gzip.conf
install -m 0644 deployment/nginx/zvms-api-preflight.conf \
  /etc/nginx/snippets/zvms-api-preflight.conf
```

在 `/etc/nginx/sites-available/zvpapi` 的 `location /api/ {` 内增加：

```nginx
include /etc/nginx/snippets/zvms-api-preflight.conf;
```

前置条件：`/etc/nginx/nginx.conf` 的 http 上下文已有 `gzip on;`。`zvms-api-gzip.conf`
只补充 `gzip_types application/json` 等参数，不开启 SSE 或二进制压缩。

## 4. 洛杉矶 Nginx 要求

目标主机：`23.169.184.163`（zgovps-la / zvp）。

宝塔的洛杉矶 Nginx 根目录不是 `/etc/nginx`，实际路径如下：

```bash
install -m 0644 deployment/nginx/zvms-cors-map.conf \
  /www/server/panel/vhost/nginx/00-zvms-cors-map.conf
install -m 0644 deployment/nginx/zvms-api-preflight.conf \
  /www/server/nginx/conf/zvms-api-preflight.conf
```

在 `/www/server/panel/vhost/nginx/zvp.xiaobopisces.top.conf` 的
`location /api/ {` 内增加：

```nginx
include /www/server/nginx/conf/zvms-api-preflight.conf;
```

洛杉矶已经启用动态 gzip，本次不重复安装 gzip 片段。

## 5. 预检行为

Nginx 对 `/api/*` 的 OPTIONS 请求直接返回 `204`，不再经过 FastAPI：

- 允许的 Origin：`zvp`、`zvpapi`、`canary` 三个正式域；
- 回显浏览器请求的 `Access-Control-Request-Headers`；
- `Access-Control-Allow-Credentials: true`；
- `Access-Control-Max-Age: 7200`；
- 未授权 Origin 不带 `Access-Control-Allow-Origin`，由浏览器立即拒绝。

新增预览域时必须同时更新 Nginx map 和后端
`ZVMS_CORS_ALLOWED_ORIGINS`，不能只改一侧。

## 6. 发布顺序

1. 备份两台的 Nginx 主配置、`zvpapi`/`zvp` vhost 和现有 conf.d/snippets。
2. 先装首尔 map、gzip 和预检 include，执行 `nginx -t`，通过后执行
   `systemctl reload nginx`。
3. 验证首尔 JSON gzip、生产域/canary 域预检、未知 Origin 拒绝。
4. 再装洛杉矶 map 和预检 include，执行 `nginx -t`，通过后执行 `nginx -s reload`
   （该机 `nginx.service` 被 panel 屏蔽）。
5. 备份洛杉矶 `/www/zvms4/frontend-dist`，发布双基址构建产物并统一属主。
6. 验证首页、API、竞速 Bundle、OPTIONS、gzip 和 SSE/文件单路行为。

## 7. 验收命令

首尔 JSON gzip：

```bash
curl -sS -H 'Accept-Encoding: gzip' -D - -o /dev/null \
  https://zvpapi.xiaobopisces.top/api/changelog
```

期望：`200`、`Content-Encoding: gzip`，且响应字节数明显小于未压缩的 29,293。

生产域预检：

```bash
curl -sS -X OPTIONS \
  -H 'Origin: https://zvp.xiaobopisces.top' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type,xuehai-id,x-device-unlock' \
  -D - -o /dev/null https://zvpapi.xiaobopisces.top/api/v3/notifications
```

canary 域预检分别请求洛杉矶和首尔，期望均为 `204`、正确回显 Origin，并包含
`Access-Control-Max-Age: 7200`。

未知 Origin 检查：

```bash
curl -sS -X OPTIONS \
  -H 'Origin: https://evil.example' \
  -H 'Access-Control-Request-Method: GET' \
  -D - -o /dev/null https://zvpapi.xiaobopisces.top/api/v3/notifications
```

期望：没有 `Access-Control-Allow-Origin`。

## 8. 回滚

1. 还原两台的 vhost/主配置备份，或移除 include 和两个 conf.d 片段。
2. 执行 `nginx -t`，通过后 reload。
3. 还原洛杉矶 `frontend-dist` 备份并修正属主。

回滚不会修改数据库，也不需要重启 FastAPI。

## 9. 2026-11-07 发布记录

| 项 | 结果 |
| --- | --- |
| 首尔配置备份 | `/root/backup-20261107-preflight-gzip-20261107-001257` |
| 洛杉矶配置备份 | `/root/backup-20261107-preflight-001323` |
| 洛杉矶前端备份 | `/www/zvms4/frontend-dist.bak-20261107-0015` |
| 首尔 gzip | `/api/changelog` 从 29,293 bytes 降至约 13,509 bytes，带 `Content-Encoding: gzip` |
| 预检 | 生产域/canary × LA/首尔 四种组合均为 204，`Max-Age: 7200` |
| 未知 Origin | 返回 204，但无 `Access-Control-Allow-Origin` |
| 前端产物 | `index-tOk_jfbe.js`，SHA256 `3EC890132D7D7004CD89763E8C16CCF060AA9879D5602F58328BD5AEAB574897` |
| 前端文件 | 336 个文件，其中 160 个 `.gz` |
| 健康检查 | 首页、首尔 `/api/health`、洛杉矶 `/api/health` 均为 200 |
