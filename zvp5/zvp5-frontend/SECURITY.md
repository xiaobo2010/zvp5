# 安全策略

本项目用于镇海中学义工时长与活动的管理（ZVP5 前端仓库）。

## 支持的版本

| 分支 | 支持状态 |
| --- | --- |
| `main` | 当前受支持 |

## 如何报告漏洞

- **请不要开公开 Issue** 报告安全漏洞。
- 请使用 GitHub 的**私密漏洞报告**（Security Advisory → Report a vulnerability）渠道提交，标题写 `ZVP5 安全报告`，正文包含：影响范围、复现步骤、建议修复方式。
- 仓库所有者会在 **7 天内**给出初步回复。

## 部署者注意事项

- `settings.py`、`aes_key.txt`、RSA 私钥、Oracle wallet 与数据库密码等敏感材料**一律不入库**，请通过部署环境自行管理。
- 登录端点具备账号与 IP 双维度频控。
- 管理写操作要求 5 分钟内密码再认证。
- AI 内容审核采用 **fail-closed** 策略（审核不可用时拒绝放行）。
- 系统文件管理与 agent 文件工具只允许 `/www/zvms4` 根目录内操作（jail），越界访问返回 403 `FILES_ROOT_FORBIDDEN`。
- 前端页面展示用户隐私信息时，请部署者自行脱敏；平台可能记录页面行为（沿用上游关于 Clarity 行为记录的合规提醒），请勿在页面上直接暴露隐私数据。

## 依赖安全

- Dependabot 每周更新 pip / pnpm / GitHub Actions 依赖。
- CI 跑 Gitleaks 两套配置做密钥泄露检测。
- CI 跑 Trivy，仅对 HIGH / CRITICAL 级别依赖漏洞告警；同时执行 `pnpm audit --prod`。
