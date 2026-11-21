# 风险规则（服务端可观测数据）

封禁规则分两类证据来源：

| evaluation | 证据来自 | 求值入口 |
|---|---|---|
| `login` | 临时密码连错次数 + 设备归属 | `services/device_guard.py` |
| `report` | 客户端上报（iframe / bridge / devtools） | `services/violation_guard.py` |
| `monitor` | 服务端自己写下的行（`APP_LOGS` / `DEVICE_CHALLENGES`） | `services/risk_monitor.py` |

本文只讲第三类。它们的共同点是**不需要客户端配合**：登录失败、登录成功、设备
不匹配、临时密码签发、导出任务都会落 `APP_LOGS`，设备挑战会落
`DEVICE_CHALLENGES`，服务层按窗口计数后裁决。

## 规则清单

| trigger | 名称 | 数据源 | 默认阈值 / 窗口 | 默认 mode |
|---|---|---|---|---|
| `login_failure_burst` | 登录失败暴破 | `APP_LOGS` `ltype=login_fail` | 10 次 / 1h | `alert` |
| `ban_evasion_retry` | 封禁期内反复尝试登录 | `APP_LOGS` `ltype=login_fail` 且摘要含 `banned` | 5 次 / 1h | `ban` |
| `challenge_flood` | 设备挑战刷取 | `DEVICE_CHALLENGES` 按 `user_oid` | 8 次 / 1h | `alert` |
| `temp_password_issue_burst` | 临时密码签发过频 | `APP_LOGS` `ltype=temp_password_create`（签发人） | 20 次 / 1h | `alert` |
| `export_burst` | 导出任务过频 | `APP_LOGS` `ltype=export`（请求者） | 10 次 / 1h | `alert` |
| `login_success_churn` | 短时间反复登录 | `APP_LOGS` `ltype=user_login` | 15 次 / 1h | `alert` |
| `device_switch_burst` | 频繁更换设备 | `APP_LOGS` `ltype=device_mismatch` | 5 次 / 24h | `alert` |

两条登录规则共享 `ltype=login_fail`：`login_failure_burst` 数全部失败，
`ban_evasion_retry` 只数摘要含 `banned` 的那部分（账号处于封禁态时的尝试）。
`login_failure_burst` 默认只告警：失败记录按受害者 oid 归因，无法可靠区分
本人输错与第三方拿公开学号定向刷；管理员确认来源可信后才应显式改回 `ban`。

## mode：只告警不封禁

规则 `config` 的可选键 `mode`：

- `"ban"`（默认，缺省即此）：达到阈值写一条 `BAN_VIOLATIONS` 证据、一条
  `risk_alert` 审计与一条 `risk_ban` 审计，并按规则梯度封禁账号；
- `"alert"`：同上但**不封禁**，只留证据与 `risk_alert`。新口径先观察误报率用。

未知 mode 归一成 `ban`（见 `services/ban_rules.normalize_mode`）：不能因为一个
拼错的配置值让本该封禁的规则静默失效。

`alert` 模式的种子 `penalties` 是 JSON `null`——没有梯度，因为从不封人。

## 判定细节

- 窗口计数 `count >= threshold` 才动手；已有**生效**封禁（`util.ban.ban_is_active`，
  到期即失效）时只记新证据，不再推梯度，避免被自己的重试打到永久档。
- 角色豁免：`admin` / `department` / `secretary` 只告警不封禁
  （与设备闸、上报闸同一口径）。
- 节流：同一 `(trigger, user_oid)` 60 秒内只评估一次，进程内表超过 4096 条先清
  过期项。调用点在每一次失败登录上，没有节流就是新的日志/查询风暴入口。
- 所有异常都被吞掉（一条 `warning`）：风险规则是额外保护，不能把登录或导出
  打成 500。

## 登录失败证据

`POST /api/users/auth` 的密码不匹配分支会写一条 `ltype=login_fail`、
`level=warning` 的日志，`data` 是固定摘要：

| 场景 | data |
|---|---|
| 密码不匹配 | `登录失败（password-mismatch）` |
| 账号处于封禁态 | `登录失败（banned）` |

**提交的密码、长度、哈希一律不落库。** 同一 oid 60 秒内最多一条（进程内节流）。
学号查不到（没有 oid）时不写：没有可挂证据的主体。

## 迁移

`db/oracle/ddl/V020__risk_rules.sql` 幂等 MERGE 七条种子（`builtin=1`、
`enabled=1`），`V001__initial_schema.sql` 同步同一批种子，新实例一步到位。
`WHEN NOT MATCHED THEN INSERT`：重跑不覆盖管理员调过的阈值、窗口与 mode。

`db/oracle/ddl/V021__add_risk_and_export_indexes.sql` 给 `challenge_flood` 的
计数路径补 `device_challenges (user_oid, created_at)`，没有它每次擦边登录都要
全表扫这张只增不减的表；同一迁移给导出列表补 `export_tasks (task_start)`。

## AI 越狱规则的立即封号（2026-10-29）

内置规则 `agent_jailbreak`（`trigger=agent_jailbreak`，默认 `mode=ban`）不再有「首次只警告」：

- **判定成立即封**：`services/violation_guard.py:record_agent_jailbreak` 在证据入库后直接调 `device_guard.apply_account_ban`，档位取该规则 `penalties` 的**第一档**；第二次违规自然进第二档（梯度由历史流水算出）。
- **警告仍写**：`USER_WARNINGS` 账本与 `agent_jailbreak_warning` 审计照写，但它是记录，不是「这次先放过」。
- **证据去重**：同一份信号 5 分钟内重复到达（客户端重发原请求）只算一次；`recorded == 0` 时直接返回，不重复处罚。
- **已在封禁中**：只记新证据，不再落封、不再发警告（否则重试会把自己推到永久档）。
- **豁免角色**：`admin` 在 `AGENT_JAILBREAK_EXEMPT_ROLES` 里——审核层本就旁路 admin，自动封管理员可能把平台锁死。`department` 不在豁免名单内。
- **失败语义**：证据/警告写库失败不改变「本次拒绝」；封禁落库失败也不回滚已入库证据，只记异常等运维补封。
- **对外**：只有 `AI_SAFETY_REJECTED` 与固定文案，不暴露档位、天数或规则名（规则名只进 `ban_violations` 的内部证据）。

审核侧语义（fail-closed、判定不可用时不记违规）见仓库根 `zvp5/docs/AI_AGENT_OPS_DESIGN.md` §10.2。
