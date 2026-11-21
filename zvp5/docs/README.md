# ZVP5 文档索引

本目录是 ZVP5 的**设计 / 契约 / 运维文档**集合，共 48 个 Markdown 文件（含本索引）。面向使用者的完整文档站维护在独立仓库 [zvp-docs](https://github.com/xiaobo2010/zvp-docs)（在线阅读：<https://xiaobo2010.github.io/zvp-docs/>）；本目录面向仓库内的开发者与维护者。

> `前端设计准则.md` 在本目录**只是一个指针**，权威版本在仓库根 [`前端约定/前端设计准则.md`](../../前端约定/前端设计准则.md)。

## 现行文档（设计 / 契约 / 规范）

| 文档 | 主题 |
|---|---|
| [`API_STRUCTURE.md`](./API_STRUCTURE.md) | 后端结构与约定（可直接照抄的实现指南） |
| [`ADR_API_V1_REVERSIONING.md`](./ADR_API_V1_REVERSIONING.md) | ADR：API 全量归并到 /api/v1，v2/v3 退役 |
| [`AI_GATEWAY_API.md`](./AI_GATEWAY_API.md) | AI 网关 API 契约 |
| [`AI_CONVERSATION_DESIGN.md`](./AI_CONVERSATION_DESIGN.md) | AI 会话管理 + 公开渠道契约（冻结稿 v2） |
| [`AI_AGENT_OPS_DESIGN.md`](./AI_AGENT_OPS_DESIGN.md) | ZVAgent 运维设计（文件 / SSH / 数据库） |
| [`AGENT_SCRIPTS.md`](./AGENT_SCRIPTS.md) | ZVAgent 脚本工具（script_catalog / script_run） |
| [`FILES_API_DESIGN.md`](./FILES_API_DESIGN.md) | Files API 设计（照片上传 / 义工单取证） |
| [`DEVICE_TRACKING_DESIGN.md`](./DEVICE_TRACKING_DESIGN.md) | 设备追踪 / 学海 ID 日志设计（APP_LOGS.XUEHAI_ID） |
| [`DB_PERF_MAINTENANCE_DESIGN.md`](./DB_PERF_MAINTENANCE_DESIGN.md) | 数据库性能采集 + 清理维护编排设计契约 v1 |
| [`REDIS_STATE_DESIGN.md`](./REDIS_STATE_DESIGN.md) | Redis 缓存与共享运行态设计 |
| [`ADB_SCHEMA_SPEC.md`](./ADB_SCHEMA_SPEC.md) | ADB Schema Spec — Mongo(zvms_new) → Oracle ADB |
| [`V4242_FIXES_AND_OPS_DESIGN.md`](./V4242_FIXES_AND_OPS_DESIGN.md) | v4.2.4-2 契约：缺陷修复 + 系统运维能力 |
| [`V4242_STAGE2_CONTRACT.md`](./V4242_STAGE2_CONTRACT.md) | v4.2.4-2 Stage 2 契约（冻结） |
| [`WEBSSH_MULTI_TARGET.md`](./WEBSSH_MULTI_TARGET.md) | Web SSH 多目标（单网关） |
| [`REDACTION_POLICY.md`](./REDACTION_POLICY.md) | 文档脱敏约定 |
| [`愚人节UI_普通用户设计稿.md`](./愚人节UI_普通用户设计稿.md) | 普通用户端 UI 设计规范 |
| [`design/AI_GATEWAY_UI_DRAFT.md`](./design/AI_GATEWAY_UI_DRAFT.md) | AI Gateway UI 视觉重构草案 |

## 历史文档（发布 / 事故 / 审计 / 迁移过程记录，保留可查）

| 文档 | 主题 |
|---|---|
| [`ADB_MIGRATION.md`](./ADB_MIGRATION.md) | ADB 迁移计划（MongoDB → Oracle Autonomous Database） |
| [`AUDIT_2026-11-02.md`](./AUDIT_2026-11-02.md) | 平台终审报告（2026-11-02） |
| [`AUDIT_2026-11-14_NIGHT_REVIEW.md`](./AUDIT_2026-11-14_NIGHT_REVIEW.md) | 夜间代码审查与优化（2026-11-14 → 11-15） |
| [`CI_CD_2026-11-08_GITHUB_AUTOMATION.md`](./CI_CD_2026-11-08_GITHUB_AUTOMATION.md) | GitHub 自动化流水线移植与审查 |
| [`CORS_PREFLIGHT_2026-11-07.md`](./CORS_PREFLIGHT_2026-11-07.md) | CORS 预检延迟调查与修复 |
| [`OVERHAUL_2026-11-08_ZVP5.md`](./OVERHAUL_2026-11-08_ZVP5.md) | ZVP5 大修总账（2026-11-07 ~ 11-08） |
| [`PERF_CANARY_2026-11-08_TABLET_SCROLL.md`](./PERF_CANARY_2026-11-08_TABLET_SCROLL.md) | canary 平板滚动流畅性优化 |
| [`PERF_SECURITY_AUDIT_2026-11-11.md`](./PERF_SECURITY_AUDIT_2026-11-11.md) | canary 前端性能与后端安全审计修复 |
| [`PRE_PUBLIC_CHECKLIST.md`](./PRE_PUBLIC_CHECKLIST.md) | 转公开仓库检查清单 |
| [`REFERENCE_TREES_ALIGNMENT.md`](./REFERENCE_TREES_ALIGNMENT.md) | 参考树对齐清单（zvp-latest / zvp5-gitlab → zvp5） |
| [`RENAME_2026-11-14_ZVMS_TO_ZVP5.md`](./RENAME_2026-11-14_ZVMS_TO_ZVP5.md) | zvms → zvp5 改名记录 |
| [`REFACTOR_2026-11-06_NIGHT.md`](./REFACTOR_2026-11-06_NIGHT.md) | 夜间重构记录（API 结构 / 性能 / 可维护性） |
| [`STATIC_ASSET_SPLIT_2026-11-12.md`](./STATIC_ASSET_SPLIT_2026-11-12.md) | 静态资源拆域到 zvpapi |
| [`ISSUE_2026-11-02_401_TOKEN_EXPIRED.md`](./ISSUE_2026-11-02_401_TOKEN_EXPIRED.md) | 登录 401 Token expired 定位报告 |
| [`ISSUE_2026-11-04_BACKUP_MISSING_UNIT.md`](./ISSUE_2026-11-04_BACKUP_MISSING_UNIT.md) | 手动备份失败：主机迁移漏 adb-sync 单元 |
| [`DEPLOY_API_SPLIT.md`](./DEPLOY_API_SPLIT.md) | 内容站 / API 直连拆分部署 |
| [`DEPLOY_DIRECT_API.md`](./DEPLOY_DIRECT_API.md) | 浏览器直连 API（2026-11-01） |
| [`DEPLOY_FRONTEND_HTTP2.md`](./DEPLOY_FRONTEND_HTTP2.md) | 前端源站回前端 + HTTP/2 与连接复用 |
| [`DEPLOY_FRONTEND_TO_BACKEND_HOST.md`](./DEPLOY_FRONTEND_TO_BACKEND_HOST.md) | 前端源站落在后端机（源站变更记录） |
| [`DEPLOY_FRONTEND_V4.2.2-1.md`](./DEPLOY_FRONTEND_V4.2.2-1.md) | zvp-frontend v4.2.2-1 上线清单 |
| [`DEPLOY_RACE_GZIP_PREFLIGHT.md`](./DEPLOY_RACE_GZIP_PREFLIGHT.md) | 双路竞速扩展、API gzip 与预检部署要求 |
| [`DEPLOY_REVPROXY_UNIFIED_2026-11-04.md`](./DEPLOY_REVPROXY_UNIFIED_2026-11-04.md) | 恢复「前端服务器统一反代」拓扑 |
| [`DEPLOY_ZVP_ORIGIN_2026-11-10.md`](./DEPLOY_ZVP_ORIGIN_2026-11-10.md) | Origin=zvp 部署与缓存刷新规范 |
| [`DEPLOY_COLDSTART_ZVPAPI_2026-11-11.md`](./DEPLOY_COLDSTART_ZVPAPI_2026-11-11.md) | 冷启动加速：首屏资源改走 zvpapi |
| [`DEPLOY_2026-11-07_NIGHT.md`](./DEPLOY_2026-11-07_NIGHT.md) | 夜间重构 + TTL 拉长 + canary 同步发布记录 |
| [`DEPLOY_2026-11-08_V1_CUT.md`](./DEPLOY_2026-11-08_V1_CUT.md) | /api/v1 干净切换 + V052 删列 + canary 重发 |
| [`DEPLOY_2026-11-12_BACKEND_HARDENING.md`](./DEPLOY_2026-11-12_BACKEND_HARDENING.md) | 后端鲁棒性与性能收口发布记录 |
| [`DEPLOY_2026-11-12_CANARY_PROMOTE.md`](./DEPLOY_2026-11-12_CANARY_PROMOTE.md) | canary 转正发布记录 |
| [`DEPLOY_2026-11-13_UI_NOTIFY_BATCH.md`](./DEPLOY_2026-11-13_UI_NOTIFY_BATCH.md) | 前端四项改造 + 通知已读上云发布记录 |

## 文档维护约定

- 新增**设计文档**放本目录，并在上方索引表登记。
- **过程记录**（发布 / 事故 / 审计 / 迁移）文件名带日期前缀 `YYYY-MM-DD` 主题，归入「历史文档」组。
