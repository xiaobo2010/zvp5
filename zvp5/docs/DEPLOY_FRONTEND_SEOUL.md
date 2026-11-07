# 前端迁回首尔(zvp.xiaobopisces.top 源站变更记录)

> 2026-11-01 深夜 ~ 11-02 凌晨。起因:LA 源站 443 大面积不可达的生产事故;处置:执行既定「方案 A」(前端 dist 搬 tencent-43 + DNS 翻转)。
> 前置阅读:`DEPLOY_DIRECT_API.md`(浏览器直连 API 的上一阶段)、`DEPLOY_API_SPLIT.md`(域名拆分)。

> **2026-11-02 后续（本文定性已被推翻）**：LA 443 不可达的真实原因是宝塔面板的
> 国家级 443 DROP 规则（探针出口恰好都在名单内），不是供应商封锁。防火墙已修复，
> 前端源站已迁回 LA，并开启 HTTP/2 与连接复用。现行拓扑、流水线与验证见
> `DEPLOY_FRONTEND_LA_HTTP2.md`；本文保留为事故过程与「首尔热备」配置记录。

## 1. 事故

- 症状:家庭宽带(本机 Clash fake-IP 分流)访问 `https://zvp.xiaobopisces.top/` TLS 失败(curl code 000,~5s);HTTP :80 正常 301。
- 多视角复测:

| 视角 | zvp:443 | 说明 |
|---|---|---|
| LA 本机回环 / 经自身公网 IP | 200 | nginx 正常,80/443 均在监听,`nginx -t` 通过,证书有效,无 OOM,ufw inactive |
| 家庭宽带 → IP 直连(无 SNI) | 200 | 浙江移动直连可通 |
| 家庭宽带 → 域名(经 Clash 出口) | 死 | 出口到 LA:443 不通 |
| tencent-43(首尔) | TCP 超时 | :80 正常 301 |
| oci-arm | TCP 超时 | — |

- 定性:LA(zgovps-la,23.169.184.163)的 443 对相当一部分网络不可达(供应商侧路由/过滤,机内无法修复),叠加家庭代理出口同样被断。前端源站放 LA = 相当多用户打不开;API(zvpapi,首尔)所有视角健康。
- 附:LA 上 systemd `nginx` 单元 inactive 是宝塔 init 启动方式,非故障。

## 2. 处置(方案 A,全部带备份,时间 = tencent-43 本地时间 2026-11-01)

1. **dist 迁移**:本地构建产物(176 文件,与 9385201 部署版一致)rsync → `tencent-43:/www/zvms4/frontend-dist/`;旧目录备份为 `frontend-dist.pre-planA-20261101-231703`。
2. **证书应急**:LA `/www/server/panel/vhost/ssl/zvp.xiaobopisces.top/{fullchain,privkey}.pem` 拷至 `tencent-43:/etc/nginx/ssl/zvp/`(后已被 certbot 取代,文件保留作应急)。
3. **tencent-43 nginx**:
   - `sites-available/zvpapi`:`location /` 从 `return 404` 改为 SPA(root `/www/zvms4/frontend-dist`、`gzip_static`、`/assets/` expires 1y、try_files → index.html);CSP map `$zvms_csp` 从 LA vhost 平移(带 `Xuehai-Id` 收紧/普通两档);备份 `zvpapi.bak-planA-20261101-231718`。
   - 新增 `sites-available/zvp`:静态 vhost + **本机 API 反代**(:80 acme + 301;:443 SPA;`= /api/system/ssh/ws` 升级、`^~ /api/`、`/socket.io/` 全部指向 `127.0.0.1:8018`,与 zvpapi vhost 同上游同参数)。机器本地端点(SSH 终端/文件管理器)由前端 `src/utils/apiRouting.ts` 路由到页面源,同机部署下零额外跳数——这正是本 vhost 反代 `/api` 的原因,与仓库 `deployment/zvms4.nginx.conf` 模板同口径。**刻意不开 http2**:WS 的 `Upgrade` 头在 h2 里非法,会表现为后端 404(Web SSH 是管理员关键路径,不冒任何险)。备份 `zvp.bak-coloc-20261101-234552`。
4. **DNS**:Cloudflare `zvp` A 记录 `23.169.184.163 → 43.128.154.199`(record_id `dc1b0099e5265d030242f927e63c80c6`,灰云,TTL 300)。
5. **证书本地化**:certbot HTTP-01(webroot `/var/www/letsencrypt`)在 tencent-43 签发,zvp vhost 切到 `/etc/letsencrypt/live/zvp.xiaobopisces.top/`(有效至 2027-01-30,certbot 定时器自动续期)。
6. **gzip 空转修复**(见 §4)。

## 2.1 同夜后续:与 albxy 的「api 直连」工作流合并

推送时发现远端 main 已有 albxy 的三个平行提交(`b4ce389` / `a475efc` / `4bf3d11`,10-31 21:13~22:44,与本仓 9385201 同一晚在另一台机器上做的同主题工作):部署模板(`deployment/`)、API 归属分层(`src/utils/apiRouting.ts`:`/system/ssh/*`、`/system/files/*` 走 content host,其余走 API host)、`VITE_API_BASE` 构建期强校验(`apiBaseConfig.ts`,生产构建缺失即报错)、CORS expose_headers 扩列。合并方式:rebase 本地三提交到远端之上,`ssh.ts`/`ssh.spec.ts` **采纳远端参数化实现**(`buildSshWsUrlFor`,语义覆盖本地版本),`main.py` 双边改动干净融合。合并后全栈重验:pytest **4165/4165**、vitest **997/997**、vue-tsc 零错误。本 vhost 的 `/api` 反代(§2 第 3 条)即为使该分层设计在「content host=后端同机」新拓扑下成立而加。

## 3. 验证记录(可复跑)

- 解析:`gail.ns.cloudflare.com`(权威)与 tencent-43 本地解析均返回 43.128.154.199。
- DNS 翻转前 `--resolve` 预检(钉首尔 IP):zvpapi 根 200(SPA + CSP/HSTS 头);SNI=zvp 打首尔 200 且证书链完整校验通过;`/api/` 418(UA 盾牌对非浏览器 UA 的预期行为,见 §6);`/socket.io/` 路由通;`/api/system/ssh/ws` 握手 101。
- CORS 预检:`Origin: https://zvp.xiaobopisces.top` → 200 + `access-control-allow-origin/headers`(authorization, content-type, xuehai-id)齐备;恶意 Origin → 400 且无 ACAO。
- 翻转后家庭端到端(Clash DNS 缓存过期后自动恢复):`https://zvp.xiaobopisces.top/` 200 → bundle gzip wire 167,848B;`https://zvpapi.xiaobopisces.top/` 200(SPA)。
- 真实流量:tencent-43 zvp vhost access log 已见外部来源 200 记录。

## 4. gzip 空转修复

- 发现:dist 无任何 `.gz`;LA 与首尔 nginx 的 `gzip_types` 均未覆盖 JS/CSS(首尔主配置该行被注释)→ `index-*.js` 一直裸传 445KB。
- 修:发布前对文本产物 `gzip -9k` 预压缩(176 → 330 文件),`gzip_static` 生效;实测 index.js 线上 wire size **445KB → 168KB(-62%)**,index.html → 739B。
- **注意**:该步骤已纳入 §5 流水线,漏掉则新构建会裸传。

## 5. 前端发布流水线(现行;替代 DEPLOY_DIRECT_API.md 中指向 LA 的命令)

```bash
cd zvms4-modified-main/zvms4-frontend
VITE_API_BASE=https://zvpapi.xiaobopisces.top/api npm run build
# ↑ prebuild 自动跑 design:sync；build 内含 type-check。
#   VITE_API_BASE 必须显式注入：缺省 '/api' 是相对基址，而 zvp 源的 vhost
#   不代理 /api，从 zvp 域名进来的页面会全部打到不存在的路径。
find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' \
  -o -name '*.json' -o -name '*.html' \) -exec gzip -9k {} \;
rsync -az --delete dist/ tencent-43:/www/zvms4/frontend-dist/
ssh tencent-43 'chown -R www-data:www-data /www/zvms4/frontend-dist'
```

构建产物里 `VITE_API_BASE` 为绝对基址(zvpapi),与同源部署兼容(同源请求不受 CORS 约束);两个 origin(zvp / zvpapi)都能正确取到 API。后端 CORS 白名单已含 `https://zvp.xiaobopisces.top` 与 `https://zvpapi.xiaobopisces.top`。

### 后端发布(现行)

```bash
rsync -az \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude 'settings.py' --exclude 'settings.py.bak.*' \
  --exclude '*.pem' --exclude 'aes_key.txt' --exclude 'ssh_key' \
  --exclude 'export/' --exclude 'data/' \
  zvms4-modified-main/zvms4-backend-python/ tencent-43:/www/zvms4/backend/
ssh tencent-43 'chown -R www-data:www-data /www/zvms4/backend && systemctl restart zvms4'
```

注意(2026-11-02 踩过):rsync `-a` 以 root 运行会保留本地属主(本地 uid 1000 在腾讯机上
映射为 `lighthouse`),服务以 www-data 运行,代码可读(644)无碍,但**根目录属主一变,
SQLite(`system_state.db` / `perf_samples.db`)就建不了 journal 文件**,表现为
`attempt to write a readonly database` 且维护态/性能采样静默降级。**rsync 后必须 chown。**

## 6. 回滚与注意事项

- **DNS 回滚**:Cloudflare 把 zvp A 记录改回 23.169.184.163(TTL 300,~5 分钟生效)。前提:LA 的 443 已恢复可达——本次事故即因此而起,回滚前先验证 `curl --resolve zvp.xiaobopisces.top:443:23.169.184.163 https://zvp.xiaobopisces.top/`。
- **LA 侧**除对旧后端机密文件 `chmod 600`(加固)外完全未动:zvp vhost(含 CSP 与 `/api` 反代回滚路径)、frontend-dist 均保留;DNS 翻转后 LA 不再收到 zvp 流量。
- tencent-43 回滚件:
  - `/etc/nginx/sites-available/zvpapi.bak-planA-20261101-231718`(翻转前 vhost)
  - `/www/zvms4/frontend-dist.pre-planA-20261101-231703`(旧 dist)
  - `/etc/nginx/ssl/zvp/`(LA 拷贝版证书,应急)
  - `/etc/nginx/sites-available/zvp.bak-coloc-20261101-234552`(加 /api 反代前的纯静态版)
- **UA 盾牌**:后端 `main.py` 对非 Mozilla UA 一律 418;监控/脚本探测请带浏览器 UA,否则测到的 418 不是故障。
- 运维遗留(待后续处理):
  - LA `/www/wwwlogs/zvp.xiaobopisces.top.log` 已 147MB 未轮转;
  - tencent-43 `sites-enabled` 里 3 个 `zvms4.bak-*` 残留(同名冲突告警,加载无影响);
  - LA 旧后端 `/www/zvms4/backend` 含过期机密副本(已 chmod 600,建议随机器退役一并清理)。