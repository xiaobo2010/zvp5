# zvp5 — 平台源码目录

ZVP5（镇海中学义工管理平台）的全部源码与随仓文档。仓库整体说明见
[根 README](../README.md)，完整文档站见 <https://xiaobo2010.github.io/zvp-docs/>。

## 目录

| 路径 | 内容 |
| --- | --- |
| `zvp5-backend-python/` | FastAPI 后端（见其 [README](zvp5-backend-python/README.md)） |
| `zvp5-frontend/` | Vue 3 + Vite 前端（见其 [README](zvp5-frontend/README.md)） |
| `docs/` | 设计、契约、运维文档，索引见 [docs/README.md](docs/README.md) |
| `deployment/` | 生产部署配置：systemd / nginx / GitHub Actions / 预览 Worker，索引见 [deployment/README.md](deployment/README.md) |

## 简要说明

- **后端**：FastAPI + SQLAlchemy 2.0 async + Pydantic，Oracle Autonomous Database 是唯一
  运行时数据库（版本化 DDL 在 `zvp5-backend-python/db/oracle/ddl/`）。分层与路由组织约定见
  [`docs/API_STRUCTURE.md`](docs/API_STRUCTURE.md)。
- **前端**：Vue 3 + Vite + TypeScript + Element Plus + Pinia，PWA 化，中英双语。
  样式的唯一事实来源在仓库根 [`前端约定/`](../前端约定/README.md)，构建时自动同步到
  `zvp5-frontend/src/assets/styles/yg/`。
- **AI 能力**对外统一称 **ZVAgent**，端点在 `/api/ai/*`，契约见
  [`docs/AI_GATEWAY_API.md`](docs/AI_GATEWAY_API.md)。

## 历史存档

本仓库的早期工程记录、模块移植审计、旧平台笔记与上游技术报告快照已移出本目录，
统一归档在 [`docs/history/`](../docs/README.md)，需要追溯时去那里查阅。
