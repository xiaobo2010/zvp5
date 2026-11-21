# 部署（deployment）

本目录是 ZVP5 的**生产部署配置与脚本**。仓库不含 Dockerfile / docker-compose：
生产形态是 **systemd + nginx + uvicorn**，静态资源由 nginx 直接提供，数据库是
Oracle Autonomous Database。

## 目录

| 路径 | 内容 |
| --- | --- |
| `zvp5.service` | 后端 systemd unit（`uvicorn main:app`，监听 `127.0.0.1:8018`） |
| `zvp5.nginx.conf` | 站点反代模板（HTTP/2、TLS、静态资源、`/api` 与 WebSocket 升级） |
| `nginx/` | 分项 nginx 片段（按 location 拆分） |
| `redis-zvp5.conf` | Redis 样例配置（可选读穿缓存） |
| `configure_runtime.py` | 运行时配置生成（写 `settings.py` 所需项） |
| `adb-backup/` | 数据库备份：安装脚本 + systemd timer + `sync_adb.py`，见 [`adb-backup/README.md`](./adb-backup/README.md) |
| `github-actions/` | 服务端部署侧：免密账号引导、部署/回滚主脚本、Actions 配置说明，见 [`github-actions/README.md`](./github-actions/README.md) |
| `preview-worker/` | canary 分支的前端预览站（Cloudflare Worker），见 [`preview-worker/README.md`](./preview-worker/README.md) |

## 两种发布通道

### 生产（release 触发）

推送到 `main` **只跑 CI，不部署**。发布一个**非预发布 GitHub Release** 后：

1. 五类门禁必须全过（后端、前端、E2E、工作流与密钥扫描、依赖漏洞）；
2. release commit 必须属于 `main`；
3. 满足以上才由 `deploy-production` job 通过 SSH 把后端与前端分别发到两台机器；
4. 健康检查失败则**按相反顺序回滚**（先回前端，再回后端）。

**数据库 DDL 永不自动执行**——脚本只在检测到 DDL 变更时告警，需要人工执行。

### 预览（canary 分支）

推送到 `v5-canary` 会跑同一套门禁，然后由 `preview` job 把前端构建发布到
Cloudflare Worker 预览站，跨域直连正式 API。预览站的放行由 canary environment
的 reviewer 控制。

## 首次部署

服务器侧的一次性准备见
[`github-actions/README.md`](./github-actions/README.md) 的「One-time server setup」：
创建 `github-deploy` 账号、安装 `zvp5-deploy` 脚本、配置四条免密 sudo 规则、
放行 GitHub Actions 网段。两台机器都需要预装 `rsync`、`curl`、`runuser`、
`sha256sum`、`gzip`、`tar`、`systemctl` 与可用的 Python venv。

## 安全提醒

- 部署私钥只存在于 GitHub `production` environment 的 Secrets 里，**不落本机**。
- 本目录下的文件由仓库所有者 review（见 `.github/CODEOWNERS`）——改动这里等于
  改动发布链路。
- 文档中一律使用占位符（`<BACKEND_IP>`、`<INTERNAL_HOST>` 等），真实地址不写进
  仓库；规则见 [`../../zvp5/docs/REDACTION_POLICY.md`](../../zvp5/docs/REDACTION_POLICY.md)。
