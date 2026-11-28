# 安全策略（Security Policy）

本仓库是公开仓库（public）。请在提交 Issue 前先读本页。

历次安全事件复盘见 [`docs/security/`](docs/security/)。

## 一、报告漏洞

**不要用公开 Issue、讨论区或 PR 报告漏洞。**

请使用 GitHub 私密漏洞报告渠道：仓库页面 → `Security` → `Report a vulnerability`
（<https://github.com/xiaobo2010/zvp5/security/advisories/new>）。

报告内容建议包含：

1. 受影响组件与版本（commit SHA 或 release 标签）；
2. 问题类型与影响范围；
3. 可复现步骤或最小复现请求；
4. 你建议的修复方向（可选）。

仓库所有者会在 **7 天内**给出初步回复（确认收到 + 是否受理 + 预计修复节奏）。
修复完成后会在同一私密渠道告知你，并视情况发布一条公开的安全公告。

已在公开 Issue / PR 中披露的漏洞视为已公开，不再走私密流程。

## 二、支持范围

| 版本 | 是否受理 |
| --- | --- |
| `main` 分支当前状态 | 是 |
| 最新一个 GitHub Release | 是 |
| 历史 release / 历史分支 | 否（仅欢迎升级建议） |

## 三、部署者自查清单

自部署本项目时，下面这些是本仓库实现中已经收紧的点，请确认你的部署没有绕过它们：

### 凭证

- `settings.py`、`aes_key.txt`、RSA 私钥、Oracle wallet 与 wallet 密码、
  AI 网关 API Key **全部不入库**（`.gitignore` 已覆盖，但 `git add -A` 前请自查）。
- 生产环境请使用最小权限数据库账号；DDL 迁移**永不自动执行**，需人工执行。

### 应用层安全基线（当前实现）

| 面 | 现状 |
| --- | --- |
| 登录与会话 | 账号 5 次/分 + IP 60 次/分双维度频控（429 `AUTH_RATE_LIMITED`）；登出轮换 `session_id`，旧 token 立即失效；JWT 带必需 `sid` 声明 |
| 弱密码 | 服务端强制改密 |
| 管理写操作 | 需 5 分钟内密码再认证令牌（`require_password_verification`），缺失即 403 |
| 重查询 | 60 次/分/用户读预算（429 `READ_RATE_LIMITED`） |
| AI 内容安全 | ZVAgent 提交走「规则 + LLM 灰区判定」且 **fail-closed**；判定不成立时拒绝本次回答但不计违规，判定成立记违规并按 `BAN_RULES.penalties` 首档封号；对外统一 `AI_SAFETY_REJECTED` |
| 文件边界 | 系统文件管理与 agent 文件工具共用根目录 jail，只允许 `/www/zvms4`，越界 403 `FILES_ROOT_FORBIDDEN` |
| 审计 | `APP_LOGS` 记录 `client_ip` / `user_agent`；日志端点 GET-only 防篡改；AI 调用流水只存元数据，正文仅落受控留存表且读取留痕 |
| 响应头 | 统一 nosniff / DENY / Referrer-Policy / HSTS / CSP；shield 命中 418 并记 `security_alert` |
| Web SSH | 目标由服务端配置决定，浏览器只发目标 `id`；响应与日志不回显密钥路径或 known_hosts；交互终端需双人 TOTP + 120 秒一次性 ticket（每人 2 条 / 全局 4 条并发上限），并记 `web_ssh` 审计 |

### 隐私合规

平台会记录用户的平台行为与活动时间。上线前请确认：

- 页面与通知中不直接展示学生姓名、学号、班级名单等个人信息；
- 日志与报表的留存期符合你所在组织的要求；
- 部署环境的访问控制与备份策略符合你的合规要求。

### 依赖与流水线

- Dependabot 每周分组更新 pip / pnpm / GitHub Actions。
- CI 运行 Gitleaks（常规 + 高置信度两套配置）、Trivy（仅 HIGH / CRITICAL）与
  `pnpm audit --prod`。
- 生产部署只在**非预发布 GitHub Release 发布后**触发，且要求 release commit
  属于 `main`、五类门禁全过、health check 通过；失败按相反顺序回滚。
  DDL 变更只告警、不自动执行。

## 四、范围外

以下不作为漏洞受理，请走普通 Issue 或 PR：

- 缺少速率限制导致的理论性 DoS（我们只在登录与重查询面做了频控）；
- 自部署方的弱口令、开放到公网的数据库或未加 TLS 的反代；
- 上游 `zvms/zvms4` 原仓库中既有的设计取舍；
- 未按 [`zvp5/docs/REDACTION_POLICY.md`](zvp5/docs/REDACTION_POLICY.md) 做脱敏导致的信息泄露（那属于文档问题，直接提 PR 即可）。

## 五、致谢

报告者姓名与发现细节会在修复公告中致谢（除非你要求匿名）。
