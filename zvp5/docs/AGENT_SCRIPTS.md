# ZVAgent 脚本工具（`script_catalog` / `script_run`）

> 状态：实现已落地（`services/agent/tools_scripts.py`），本文说明**怎么新增一个可被
> agent 调用的脚本**。基线口径见 `docs/AI_AGENT_OPS_DESIGN.md` §0 的不变量，本文只
> 讲脚本这一层。

---

## 1. 为什么脚本要单独一层

`ssh_exec` / `fs_write` 已经能让管理员（经提案 + TOTP）执行任意命令、写任意文件，
所以脚本工具**不是**为了「扩大能力」，而是为了把一类重复的运维动作**收敛成固定入口**：

| | `ssh_exec` | `script_run` |
|---|---|---|
| 执行什么 | 任意命令行 | `SCRIPT_REGISTRY` 里的固定脚本 |
| 参数 | 一整行 shell | 结构化（`before` / `apply` / `backup_dir`），argv 列表 |
| 默认行为 | 立即产生副作用 | **dry-run**，`apply=true` 才写库 |
| 提案预览 | 一行命令 | argv 逐项 + `apply` 标记 |
| 确认 | 另一位管理员 TOTP | 另一位管理员 TOTP（同一条） |

**白名单不是自动发现**：`SCRIPT_REGISTRY` 是写死的常量，不扫 `scripts/` 目录。目录扫描
会把「某次调试时顺手放进 `scripts/` 的脚本」变成模型可执行的远程代码——那是把代码
评审的边界让给了文件系统。新增脚本必须显式登记 + 过 review + 补测试。

---

## 2. 现有脚本

| name | path | 作用 | `--before` 口径 |
|---|---|---|---|
| `clear_device_bans` | `scripts/clear_device_bans.py` | 清空显式设备封禁与梯度计数 | 授权戳（`YYYY-MM-DD-HHMMSS`，agent 侧补 `-000000`） |
| `purge_legacy_device_bindings` | `scripts/purge_legacy_device_bindings.py` | 删除早于 `before` 的历史设备绑定 | 删除边界（`YYYY-MM-DD`） |

两者都遵守同一份脚本纪律：默认 dry-run、`--apply` 才写、写前先导出 JSON 备份到
`data/backups`（生产即 `/www/zvms4/backend/data/backups`；可用 `--backup-dir` 改，
但必须在该目录下）。**不要用 `/tmp`**：后端 service 开了 `PrivateTmp=true`，服务看到
的 `/tmp` 是私有挂载，重启即丢，宿主机管理员也看不到备份。

---

## 3. 新增一个脚本：五步

### 3.1 放进 `scripts/`

路径必须是 `zvp5-backend-python/scripts/<name>.py`，且以 `python scripts/<name>.py`
直接可跑（脚本自己把仓库根补进 `sys.path`，见现有两个脚本的开头）。

### 3.2 首行 docstring 写清用途与安全约束

至少要回答四个问题：

1. **为什么需要它**（哪个存量问题，一次性还是周期）；
2. **它改哪张表/哪些行**，哪些看起来相关但**不在**范围内；
3. `--before` 的语义（删除边界 / 授权戳，格式是什么）；
4. 备份写到哪里、备份失败时怎么处理（必须中止，不能「没有备份的删除」）。

### 3.3 默认 dry-run，`--apply` 才写

约定（与现有一致，新增脚本照抄）：

- 无参数 = 只打印统计/样例，**不写任何东西**；
- `--apply` 必须同时给 `--before`（拒绝「无授权戳的全表清空」）；
- 删/改前先把待处理行导出成 JSON 备份，**备份失败立即中止**；
- 非 0 退出码表示失败（agent 会把 stderr 原样回给模型，让它纠偏）。

### 3.4 在 `SCRIPT_REGISTRY` 登记

`services/agent/tools_scripts.py` 里加一条：

```python
"my_script": {
    "name": "my_script",
    "path": "scripts/my_script.py",
    "title": "一句话标题",              # UI / catalog 用
    "description": "它改什么、不改什么",   # 给模型看的边界说明
    "usage": ".venv/bin/python scripts/my_script.py --before YYYY-MM-DD [--apply]",
    "params": _script_params(),          # before 必填；apply/backup_dir 可选
    "requires_confirmation": True,       # 写脚本一律 True
    "before_style": _BEFORE_STYLE_DATE,  # 或 _BEFORE_STYLE_DATETIME
},
```

注意：

- `params` 是**对外契约**：`before` 必填且是 `YYYY-MM-DD`，`apply` 布尔默认 `false`，
  `backup_dir` 可选且必须在 `data/backups` 下（缺省就写到这里）。脚本自己的参数格式与它
  不同时，在 `_before_argument` / `_argv` 里做翻译，**不要**把脚本的历史格式暴露给模型。
- 登记即进入 `script_catalog` 与 `script_run` 的 `enum`，两者都从注册表派生，无需改别的。
- 不要为了「让模型能跑」给任意脚本加参数；参数越多，提案预览能表达的意图越少。

### 3.5 补测试

`tests/test_agent_scripts_tools.py` 里至少覆盖：

- 注册表形状（路径存在、`params.required == ["before"]`、`requires_confirmation`）；
- `prepare` 的 argv（`apply=false` 不出现 `--apply`，`apply=true` 才出现）；
- 参数校验（日期格式、备份目录越界、未知脚本名 → 400）；
- **不要真的执行脚本**：`asyncio.create_subprocess_exec` 打桩，断言 argv 与输出处理。

---

## 4. 运行语义（实现细节，改之前先看）

- **提案是唯一入口**：`apply=false` 也走提案（`prepare` → 用户确认 → `impl`），不是
  direct 调用。「预演」不构成绕过确认的理由，否则写工具就有两种语义。
- **绝不经过 shell**：`asyncio.create_subprocess_exec(sys.executable, script, *argv)`，
  argv 列表逐项传参；参数里的空格、引号、`;` 都只是参数。
- **`cwd` 固定仓库根**，`env` 继承当前进程并补 `PYTHONPATH`（脚本仍自己补 `sys.path`）。
- **超时 120s**：超时 kill 子进程并回 `AI_AGENT_TOOL_TIMEOUT`（504）。不 kill 的话，
  一次 run 结束后还有脚本在写库。
- **输出**：stdout/stderr 各截 8000 字符；密钥形态（`--password=` / `TOKEN=` 等）按
  `ops_tools.redact` 同一套正则抹掉，但**不**把长正文折成 `<len N>`（那是审计摘要口径，
  折掉等于把结果删了）。非 0 退出码是**结果**，不是异常。
- **`backup_dir` 白名单**：`resolve()` 后必须落在 `BACKUP_ROOT`
  （`<backend>/data/backups`，生产 `/www/zvms4/backend/data/backups`）内，符号链接与
  `..` 都会被挡住；未显式给定时 argv 里仍会带 `--backup-dir <BACKUP_ROOT>`——脚本自己
  的默认值是 `/tmp/opencode/backups`，在 `PrivateTmp=true` 的服务里重启即丢。

---

## 5. 不要做的事

- 不要动态发现脚本（扫目录 / 读 `scripts/*.py` 当清单）。
- 不要让 `script_run` 接受 `path` 参数或任意命令行——那就退化成第二个 `ssh_exec`。
- 不要把脚本输出整段丢进事件摘要（`RESULT_SUMMARY` ≤500 且走 `redact`）。
- 不要在脚本里自己找配置文件 / 拼 DSN：一律走 `settings` + `db.oracle.session.session_scope`。
- 不要新增第三方依赖（`AGENTS.md` 硬规则）。
