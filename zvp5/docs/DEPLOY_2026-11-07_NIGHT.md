# 2026-11-07 发布记录（夜间重构 + TTL 拉长 + canary 同步）

发布范围：`main` 分支 `c4dd352`（含 `7c75a89` 夜间重构与 TTL 拉长、`c4dd352` call_timeout 修复）。
canary 同步：`v5-canary` = `7711cdc`（合入 `771a664 重做悬浮导航并统一无框控件` + `origin/main`）。

## 一、后端（`zvp-backend`）

| 项 | 值 |
| --- | --- |
| 目标目录 | `/www/zvms4/backend`（rsync 部署，非 git） |
| 备份 | `/www/zvms4/backend.bak-20261107-120647`（排除 `.venv`，82M） |
| 变更规模 | 改 78 个文件、新增 33 个（`routers/ai/`、`db/repos/protocols/` 等）、删除 11 个死代码文件 |
| 服务 | `systemctl restart zvms4` → `active`，`/api/health` 200 |

发布用 `git archive HEAD:zvms4-modified-main/zvms4-backend-python` 导出**仅入库文件**再 rsync，
避免把本地的 `settings.py`、`*.pem`、`aes_key.txt` 覆盖到线上。生产专属文件一律 exclude，
`--delete` 因此不会误删：`.venv`、`data/`、`export/`、`system_state.db`、`perf_samples.db`、
`ssh_key*`、`settings.py`、`*.pem`、`configure_runtime.py`。

**rsync 会把属主带成本地用户**（rsync `-a` 保留 owner）。发布后必须修正：

```bash
cd /www/zvms4/backend
find . -path ./.venv -prune -o -type d -exec chmod 775 {} +
find . -path ./.venv -prune -o -type f ! -perm -u+x -exec chmod 664 {} +
find . -path ./.venv -prune -o -exec chown www-data:www-data {} +
```

### 事故：`call_timeout` 让服务启动即崩

首次发布后 `/api/health` 返回 503：

```
TypeError: connect_async() got an unexpected keyword argument 'call_timeout'
```

python-oracledb **26.x 没有把 `call_timeout` 作为 connect 关键字**（它只是连接属性），
而本机 venv 是 26.0.1、线上是 26.1.0，测试从不真连库，所以本地全绿也发现不了。

处置：先从备份还原 `db/oracle/engine.py` 恢复服务（3 分钟内回到 200），再改代码：

- `connect_args` 不再带 `call_timeout`；
- 改用 SQLAlchemy pool 的 `connect` 事件给每条新连接设置 `conn.call_timeout`
  （async 方言交给 pool 的是适配对象，真实驱动连接从 `_connection` 取）；
- 新增 `tests/test_engine_connect_args.py`：把「驱动签名」变成硬约束。

线上复核（已生效）：

```
driver call_timeout = 60000
query ok: 1
```

## 二、前端（`zvp-frontend`）

| 项 | 值 |
| --- | --- |
| 目标目录 | `/www/zvms4/frontend-dist`（nginx `root`，`gzip_static on`） |
| 备份 | `/www/zvms4/frontend-dist.bak-20261107-120647` |
| 构建 | `VITE_API_BASE=https://zvp.xiaobopisces.top/api` + `VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api` |
| 产物 | `index-Ch_yOuv0.js` / `index-ZSk6j9bi.css` / `index-legacy-VJBm00BY.js`，共 178 个文件 + 160 个 `.gz` |

`gzip_static on` 依赖同名 `.gz` 文件，发布后必须补压（css 也要，199KB → 26.7KB）：

```bash
cd /www/zvms4/frontend-dist
find . -type f \( -name "*.js" -o -name "*.css" -o -name "*.html" -o -name "*.json" \
  -o -name "*.svg" -o -name "*.xml" -o -name "*.txt" -o -name "*.webmanifest" \) \
  ! -name "*.gz" -exec gzip -9 -k -f {} +
find . -type f -exec chown www:www {} +
```

## 三、canary（Cloudflare Worker）

| 项 | 值 |
| --- | --- |
| 分支 | `v5-canary` = `7711cdc`（`771a664` + `origin/main` 合并，零冲突） |
| Worker | `zvp-yuren`，域名 `canary.xiaobopisces.top` |
| 版本 ID | `05aa7e9c-96fb-4952-a70a-d0fc7d09bdbc` |
| 产物 | `index-IaocrRZc.js` / `index-CEu3ylE-.css` |

发布前删掉 `sw.js` / `workbox-*`（预览站不跑 PWA precache），并改写 API 基址指向生产 API。
注意 Worker 的 `not_found_handling: single-page-application` 会把不存在的 `sw.js` 回成
`index.html`（200），不是 404——验收时看内容不要只看状态码。

## 四、验收结果

| 检查 | 结果 |
| --- | --- |
| 前端首页 / `/user/login` `/activities` `/ai` `/manage` `/about` | 全 200 |
| 前端 `/api/health` `/api/version` `/api/changelog` `/api/` | 全 200 |
| 后端直连 `/api/health` `/api/version` `/api/changelog` `/api/` | 全 200 |
| 后端 `/api/changelog` gzip | 200 + `content-encoding: gzip` |
| 静态 gzip_static（js / css） | 200 + `content-encoding: gzip`（css 199KB → 26.7KB） |
| 预检：生产 Origin / canary Origin（前端 + 后端） | 204 + 正确回显 Origin + `Max-Age: 7200` |
| 预检：未知 Origin | 204 但无 `Access-Control-Allow-Origin` |
| canary 页面（`/` `/user/login` `/me` `/about` `/activities` `/manage` `/ai`） | 全 200 |
| canary → API 实际请求 | 401（未登录，符合预期）+ 正确 CORS 头 |
| 前端近 2000 条访问日志 | 无 5xx |
| `zvms4` 服务 | `active`，近 10 分钟 0 条 Traceback |

## 五、回滚

1. 后端：`rsync -a /www/zvms4/backend.bak-20261107-120647/ /www/zvms4/backend/`（排除 `.venv`）→ 修正属主 → `systemctl restart zvms4`
2. 前端：`rsync -a /www/zvms4/frontend-dist.bak-20261107-120647/ /www/zvms4/frontend-dist/` → 修正属主
3. canary：把 `v5-canary` 回退到 `860ce52` 重新构建并 `wrangler deploy`
4. 不涉及数据库变更，无需回滚数据
