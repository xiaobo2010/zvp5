# Redis 缓存与共享运行态

2026-10 引入。目标有两个，分开看：

1. **减少 ADB 往返**（`db/cache.py` + `db/cached_repos.py`）——这是响应性能的主要收益；
2. **把跨请求/跨进程的运行态收拢到一处**（`db/state.py`）——限额、熔断、解锁门、授权缓存
   不再「每进程各算各的」。上一版这些状态全在进程内存里，前提是单 worker。

## 开关与故障语义

| 场景 | 行为 |
|---|---|
| `settings.REDIS_URI` 为空 / `redis` 包缺失 | 全部退化为直读 + 原有进程内实现，行为与改造前逐字节一致 |
| 连接失败 / 命令超时 | 标记 5s 降级窗口，期间不再尝试；读路径回源，写路径跳过 |
| 缓存值损坏（旧格式/脏 JSON） | 当作未命中，重新回源 |
| Redis 重启 | **缓存保留**（RDB + AOF）；万一数据目录被清空，缓存与 TTL 状态归零、限额窗口归零、解锁门失效（安全方向由鉴权层兜底），全部可从 ADB 重建 |

`REDIS_URI` 只支持 `redis://127.0.0.1:6379/0` 这种本机形态；生产 Redis **只监听
127.0.0.1**，无密码。持久化配置见 `deployment/redis-zvms.conf`：RDB + AOF 同时开启
（`appendonly yes` / `appendfsync everysec` / `save 900 1` / `save 300 10`），
重启后热缓存与 epoch 版本号都能恢复。

**边界（别记错）**：持久化只保证「进程/机器重启后不用从零预热」，**不**把 Redis
升级成业务数据的真相。用户、活动、设置、角色策略的权威副本永远在 ADB；Redis 里
的东西随时可以 `flushdb` 后按需重建。

## 一、仓储读穿缓存（性能层）

代理注入点在 `db/backends.py` 的工厂：`users` / `groups` / `activities` /
`ai_conversations` 四个仓储返回 `Cached*Repo`。传入 `session` 时整条代理直通，
事务内读永远看到未提交状态。

| 键 | 内容 | TTL | 失效 |
|---|---|---|---|
| `zvms:user:<oid>` | 用户全量文档 | 60s | 用户写操作后精确删除 |
| `zvms:auth:<oid>` | 认证窄投影 | 30s | 同上（与全量文档分开，防止投影串味） |
| `zvms:names:<ver>:<digest>` | 批量显示名 | 300s | 用户 epoch（建/删/转班/改学号） |
| `zvms:class:<ver>:<class_oid>` | 班级成员 | 300s | 同上 |
| `zvms:group:<oid>` / `zvms:pgroup:<oid>` | 班级/权限组 | 300s | 改组后删除 |
| `zvms:glist:<ver>:...` / `zvms:pglist:<ver>:...` | 组列表 | 60s | 组 epoch |
| `zvms:activity:<oid>` | 活动详情 | 60s | 写活动后删除 |
| `zvms:activities:effective:<ver>` | 有效活动全集（仅无日期区间） | 600s | 活动 epoch |
| `zvms:conv:<oid>` | AI 会话详情（>256KB 不缓存） | 30s | 写会话后按 oid 删除 |
| `zvms:convlatest:<owner>:<scene>` | 场景最近会话 | 30s | 新建会话后删除 |
| `zvms:cfg:<...>` | 设置域的派生缓存（当前无读穿对象，留给后续按需接入） | — | `flush_scopes(["settings"])` 前缀清空 |

epoch 是 `zvms:epoch:<scope>` 下的单调计数器：整类失效时 `INCR` 一次，旧 key 靠 TTL
自然淘汰。**所有 epoch key 都带 30 天 TTL**（`cache.EPOCH_TTL_SECONDS`，INCR 后紧跟
一次 `EXPIRE`）——epoch 是缓存元数据，不该有永久 key；过期等价于「回到 0」，只让旧
key 失去意义，不会读出错值。

批量失效走 `cache.flush_prefix` / `cache.flush_scopes`（`SCAN` + 批量 `UNLINK`，
没有 `UNLINK` 的客户端退回 `DELETE`；**不用 `KEYS`** —— 几十万 key 时会把 Redis 单
线程卡死）：

| scope | 覆盖的 key 前缀 |
|---|---|
| `users` | `user` / `auth` / `names` / `class` + `epoch:users` |
| `groups` | `group` / `pgroup` / `glist` / `pglist` + `epoch:groups` |
| `activities` | `activity` / `activities` + `epoch:activities` |
| `conversations` | `conv` / `convlatest` + `epoch:conversations` |
| `settings` | `cfg` + `epoch:settings` |
| `roles` | `role` + `epoch:roles` |

未知 scope 抛 `ValueError`（路由层翻 400），且在校验阶段就抛——一个 key 都不会删。

## 一·五、写事件自动刷新（版本闸）

`services/settings_runtime.py` 与 `services/roles/policy.py` 的快照原本每 5 秒各读
一次 ADB（合计约 4 条 SQL/5s，且 `ai.role_quota` 的旧值会每轮刷一条 warning）。
现在两条链路共用同一套「版本闸」：

1. 写路径（`settings_runtime.set_many` / `set_user_quota`、`policy.replace_role`）
   落库后调用 `cache.flush_scopes(["settings"|"roles"])`：清掉该域前缀 + `INCR`
   `epoch:<scope>`；
2. 读路径每 5 秒先 `cache.version_or_none("<scope>")` —— **一条 GET**。版本与上次
   相同且上次回源距今 < 5 分钟 → 直接跳过 ADB，快照继续用；
3. 版本变化 → 只读一次库，然后记住新版本；
4. `FULL_RELOAD_SECONDS = 300` 强制回源：版本号只能证明「没人通过 API 改过」，
   挡不住有人直接改库，5 分钟是它的兜底；
5. Redis 不可用（`version_or_none` 返回 `None`）→ 闸门禁用，退回原来的每 5 秒读库，
   行为不坏。

`version_or_none` 是刻意的：`version()`（= `epoch()`）读不到时返回 0，与「版本就是
0」无法区分，闸门会把「Redis 挂了」误判成「没人改过」。

顺带修掉一个生产日志噪音：`_validate_role_quota` 原来只认 `QUOTA_KINDS=("af",)`，
库里 V051 之前的 `ocr` / `chat` 会让 `ai.role_quota` 每 5 秒报一次「值不合法，使用
默认值」。现在旧 kind 归并进 `af`：同角色已有 `af` 就忽略旧键，没有 `af` 才把旧键
求和写进 `af`；归并只记一次 info（进程级去重）。

## 二、共享运行态（正确性层）

全部经 `db/state.py` 原语（滑动窗口 ZSET / KV），模块自己保留 Redis 不可用时的
进程内回退。

| 模块 | 键 | 语义 |
|---|---|---|
| `services/ai/rate_limit` | `zvms:state:win:ai:<user>:<kind>` | 滑动窗口；有 Redis 时限额即全局，不再按 worker 折算 |
| `util/login_throttle` | `...:login:acct:<账号>` / `...:login:ip:<IP>` | 账号 5/60s、IP 60/60s；账号档先判，避免连累同出口 IP |
| `util/read_throttle` | `...:read:<oid>` | 60/60s 读预算 |
| `services/ai/routing` | `...:route:fail:<key>` / `...:route:cool:<key>` | 失败计数 60s；3 次熔断 60s；`/health` 的 `cooldown_scope` 会显示 `shared` |
| `util/agent_unlock` | `...:agent:unlock:ok|lock|fail:<oid>` | 30 分钟解锁窗口 / 5 次失败锁 10 分钟 |
| `util/dual_auth` | `...:dual:fail:<op>` / `...:dual:grant:<op>:<action>` | 失败限流 5/60s；30 分钟授权缓存（读不到即要求重新输码，fail-closed） |
| `services/ai/api_keys` | `...:ai:key:<digest>` / `...:ai:key:used:<oid>` | 校验缓存 30s；`last_used_at` 采样 60s（`SET NX`） |
| `services/ai/credentials` | `...:ai:cred:<user>` / `...:ai:ssrf:<host>` | 目录缓存 30s；SSRF 判定缓存 |
| `routers/ai_router` | `...:ai:stream:<key>:<model>` | 「上游不支持流式」负缓存 1h |

## 三、仍留在进程内的状态（有意为之）

| 状态 | 位置 | 原因 |
|---|---|---|
| 设置中心 5s 快照 | `services/settings_runtime.py` | 读取点是同步函数/同步依赖，改 Redis 需要把整条调用链 async 化；数据本身在 ADB。**已加 Redis 版本闸**：每轮只花一条 GET，不读库，见「一·五」 |
| 角色策略快照 | `services/roles/policy.py` | 同上，同样走版本闸 |
| agent 执行队列 / 并发槽 / in-flight | `services/agent/run_queue.py` | `asyncio` 队列与信号量是执行态，跨进程无法共享；多 worker 前必须先重做 run 调度 |
| SSH 会话票据 | `services/ssh_bridge.py` | 票据与进程内 SSH 连接绑定，换了进程也没有连接可复用 |
| 附件每日配额 | `util/attachment.py` | 粗粒度滥用护栏、按日重置；注释已声明「best-effort」，迁移收益低 |
| perf sampler / host metrics 上一采样 | `services/perf_sampler.py`、`host_metrics.py` | 增量需要「本进程上一次」的原始值 |
| 请求级认证缓存 | `util/object_id.py`（`request.state`） | 作用域本来就是单个请求 |

## 四、运维

```bash
# 状态总览
redis-cli info keyspace
redis-cli --scan --pattern 'zvms:*' | head -20
redis-cli info stats | grep -E 'keyspace_hits|keyspace_misses'

# epoch 都该有 TTL（30 天）。ttl = -1 的 key 需要排查/补一次写：
redis-cli --scan --pattern 'zvms:epoch:*' | xargs -r -n1 redis-cli ttl

# 按域失效（与 POST /api/system/db/cache/flush 同一套 scope；下面以 roles 为例）
redis-cli --scan --pattern 'zvms:role*' | xargs -r redis-cli unlink
redis-cli incr zvms:epoch:roles
redis-cli expire zvms:epoch:roles 2592000
# 推荐直接走 API：它同时会广播版本号，别的进程下一轮就回源。

# 彻底清空（缓存可重建，状态会归零）
redis-cli flushdb

# 服务
systemctl status redis-server
```

生产机 `zvp-backend` 的 Redis 配置追加在 `/etc/redis/redis.conf` 末尾，片段见
`deployment/redis-zvms.conf`：`bind 127.0.0.1 ::1`、`protected-mode yes`、
`maxmemory 256mb`、`allkeys-lru`、`appendonly yes`、`appendfsync everysec`、
`dir /var/lib/redis`、`save 900 1`、`save 300 10`。RDB + AOF 同时开，重启可恢复
epoch 与热 key；不要再追加 `save ""`（那等于关掉 RDB）。

**已知环境坑（历史主机 `zvp-frontend`，若再上线需复查）**：该机
`/usr/local/lib/libjemalloc.so.2` 是自编译版本，`redis-server` 加载时
`failed to map segment`。已用 systemd drop-in
`/etc/systemd/system/redis-server.service.d/override.conf` 固定
`LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu`，别删。

## 五、部署注意事项

- `requirements.txt` 增加 `redis>=6.4,<9`，部署机 venv 需要 `pip install -r requirements.txt`。
- `settings.example.py` 里 `REDIS_URI = ""`；生产 `settings.py` 追加
  `REDIS_URI = "redis://127.0.0.1:6379/0"`。
- 单 worker 部署仍然是当前前提（`run_queue` 依赖进程内状态）。Redis 让限流/熔断
  具备了跨进程正确性，但要真正开多 worker，还需要先处理 agent 执行队列。
