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
| Redis 重启 | 缓存与 TTL 状态清空；限额窗口归零、解锁门失效（安全方向由鉴权层兜底） |

`REDIS_URI` 只支持 `redis://127.0.0.1:6379/0` 这种本机形态；生产 Redis **只监听
127.0.0.1**，无密码、无持久化（RDB/AOF 均关闭，重启即空，全部数据可从 ADB 重建）。

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

epoch 是 `zvms:epoch:<scope>` 下的单调计数器：整类失效时 INCR 一次，旧 key 靠 TTL
自然淘汰。

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
| 设置中心 5s 快照 | `services/settings_runtime.py` | 读取点是同步函数/同步依赖，改 Redis 需要把整条调用链 async 化；数据本身在 ADB，5s 刷新已足够 |
| 角色策略快照 | `services/roles/policy.py` | 同上 |
| agent 执行队列 / 并发槽 / in-flight | `services/agent/run_queue.py` | `asyncio` 队列与信号量是执行态，跨进程无法共享；多 worker 前必须先重做 run 调度 |
| SSH 会话票据 | `services/ssh_bridge.py` | 票据与进程内 SSH 连接绑定，换了进程也没有连接可复用 |
| 附件每日配额 | `util/attachment.py` | 粗粒度滥用护栏、按日重置；注释已声明「best-effort」，迁移收益低 |
| perf sampler / host metrics 上一采样 | `services/perf_sampler.py`、`host_metrics.py` | 增量需要「本进程上一次」的原始值 |
| 请求级认证缓存 | `util/object_id.py`（`request.state`） | 作用域本来就是单个请求 |

## 四、运维

```bash
# 状态总览
redis-cli info keyspace
redis-cli --scan --pattern 'zvms:*' | head
redis-cli info stats | grep -E 'keyspace_hits|keyspace_misses'

# 彻底清空（缓存可重建，状态会归零）
redis-cli flushdb

# 服务
systemctl status redis-server
```

生产机 `zgovps-la` 的 Redis 配置写在 `/etc/redis/redis.conf` 末尾：
`bind 127.0.0.1 ::1`、`maxmemory 256mb`、`allkeys-lru`、`save ""`、`appendonly no`。

**已知环境坑**：该机 `/usr/local/lib/libjemalloc.so.2` 是自编译版本，`redis-server`
加载时 `failed to map segment`。已用 systemd drop-in
`/etc/systemd/system/redis-server.service.d/override.conf` 固定
`LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu`，别删。

## 五、部署注意事项

- `requirements.txt` 增加 `redis>=6.4,<9`，部署机 venv 需要 `pip install -r requirements.txt`。
- `settings.example.py` 里 `REDIS_URI = ""`；生产 `settings.py` 追加
  `REDIS_URI = "redis://127.0.0.1:6379/0"`。
- 单 worker 部署仍然是当前前提（`run_queue` 依赖进程内状态）。Redis 让限流/熔断
  具备了跨进程正确性，但要真正开多 worker，还需要先处理 agent 执行队列。
