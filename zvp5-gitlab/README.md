# Zhenhai High School Volunteer Management Platform 5

## 数据库选型决策

- 目标数据库：**Oracle Autonomous Database (ADB)**，Always Free，region `ap-chuncheon-1`。
- 接入方式：`oracle+oracledb://...`（python-oracledb thin 模式，无需 Instant Client）；async 由 `create_async_engine` 自动启用。
- 选型理由：本后端是 SQLAlchemy 2.0 async ORM，方言由 `config.DATABASE_URI` 决定，ADB 无需改动连接层即可接入。
- 为兼容 Oracle 所做的类型调整：
  - `type_annotation_map` 中 `str` 由 `Text` 改为 `String(255)`（Oracle 的 `Text` = CLOB，不能用于 `=`/LIKE/GROUP BY/普通索引）。
  - `Activity.description` 改为 `String(4000)` 且可空（它参与 `GROUP BY`，不能是 CLOB；Oracle 中 `''` 等于 NULL）。
  - 长文本字段（`Notice.description`/`Notice.link`/`Resource.url`/`SavedTask.result`/`Log.summary`/`Workflow.description`）显式使用 `Text`(CLOB)，它们不参与比较或分组。
  - 其余映射天然兼容：`ObjectId` → `VARCHAR2(24)`，`Json` → `BLOB`，`IntegralEnum` → `INTEGER`，`Boolean` → `SMALLINT`（23ai 之前），`Float` → `FLOAT`。
- 运维约束（Always Free ADB）：空闲 7 天固定自动停用且不可关闭 → 需要外部保活（定时心跳写入，见下）；停用/不可用累计 90 天可能被回收；ADB 不提供 PostgreSQL 协议。

## 运行时（Python）

- **目标版本：Python 3.14**（后端与保活统一；不使用 3.15 beta 构建）。
- 下限依据（实测反推）：
  - `models/users.py:104` 的 `SYSTEM: ClassVar[Token]` 是类体内自引用注解，且全仓**无** `from __future__ import annotations` → 注解在定义时求值，需 PEP 649 延迟求值（3.14+）；在 3.12 上**导入即 `NameError: name 'Token' is not defined`**。
  - 另有 10 处 PEP 695 `type X = ...` 别名语句（≥3.12），被 3.14 覆盖。
- 驱动验证：`python-oracledb 26.0.1` 在 **3.14 导入正常**；在 `3.15.0b3`（beta 构建）上 `import oracledb` **段错误**（C 扩展 ABI 不匹配），故排除。
- 保活：部署于 zgovps-la 的 3.14 venv，cron 每 6 小时执行一次心跳写入（`keepalive_heartbeat` 表 + COMMIT），以重置 ADB 的 7 天空闲自动停计时。
