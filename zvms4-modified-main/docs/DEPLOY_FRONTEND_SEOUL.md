# 前端迁回首尔(zvp.xiaobopisces.top 源站变更记录)

> 2026-11-01 深夜 ~ 11-02 凌晨。起因:LA 源站 443 大面积不可达的生产事故;处置:执行既定「方案 A」(前端 dist 搬 tencent-43 + DNS 翻转)。
> 前置阅读:`DEPLOY_DIRECT_API.md`(浏览器直连 API 的上一阶段)、`DEPLOY_API_SPLIT.md`(域名拆分)。

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
   - 新增 `sites-available/zvp`:纯静态 vhost(:80 acme + 301,:443 SPA + http2 on)。API 仍由浏览器直连 zvpapi,本 vhost 不代理 `/api`。
4. **DNS**:Cloudflare `zvp` A 记录 `23.169.184.163 → 43.128.154.199`(record_id `dc1b0099e5265d030242f927e63c80c6`,灰云,TTL 300)。
5. **证书本地化**:certbot HTTP-01(webroot `/var/www/letsencrypt`)在 tencent-43 签发,zvp vhost 切到 `/etc/letsencrypt/live/zvp.xiaobopisces.top/`(有效至 2027-01-30,certbot 定时器自动续期)。
6. **gzip 空转修复**(见 §4)。

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

## 6. 回滚与注意事项

- **DNS 回滚**:Cloudflare 把 zvp A 记录改回 23.169.184.163(TTL 300,~5 分钟生效)。前提:LA 的 443 已恢复可达——本次事故即因此而起,回滚前先验证 `curl --resolve zvp.xiaobopisces.top:443:23.169.184.163 https://zvp.xiaobopisces.top/`。
- **LA 侧**除对旧后端机密文件 `chmod 600`(加固)外完全未动:zvp vhost(含 CSP 与 `/api` 反代回滚路径)、frontend-dist 均保留;DNS 翻转后 LA 不再收到 zvp 流量。
- tencent-43 回滚件:
  - `/etc/nginx/sites-available/zvpapi.bak-planA-20261101-231718`(翻转前 vhost)
  - `/www/zvms4/frontend-dist.pre-planA-20261101-231703`(旧 dist)
  - `/etc/nginx/ssl/zvp/`(LA 拷贝版证书,应急)
- **UA 盾牌**:后端 `main.py` 对非 Mozilla UA 一律 418;监控/脚本探测请带浏览器 UA,否则测到的 418 不是故障。
- 运维遗留(待后续处理):
  - LA `/www/wwwlogs/zvp.xiaobopisces.top.log` 已 147MB 未轮转;
  - tencent-43 `sites-enabled` 里 3 个 `zvms4.bak-*` 残留(同名冲突告警,加载无影响);
  - LA 旧后端 `/www/zvms4/backend` 含过期机密副本(已 chmod 600,建议随机器退役一并清理)。