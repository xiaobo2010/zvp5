# Vite dev server RCE / 僵尸网络事件复盘

- 复盘时间：2026-11-28
- 证据目录：内部取证包（不随仓库提交）
- 宿主机：`<HOST_IP>`（`<HOST_ALIAS>` / `<HOSTNAME>`，2C/2G，Debian，无 swap）
- 攻击窗口：2026-11-24 03:34:06 — 2026-11-28 10:31:39
- 服务停止：2026-11-28 10:59:54

> 本文件按仓库脱敏约定，不写真实基础设施 IP、密钥和密码。真实值只在证据包中；证据包禁止提交、禁止外发。

## 一、结论

这不是普通扫描，而是 **Vite dev server 被 RCE 后，该主机被当作僵尸网络节点使用**。

攻击者获得了：

- root 代码执行（进程以 `root (uid=0)` 运行）
- 环境变量读取能力
- 任意文件读取能力（至少成功读取 `/root/.codex/installation_id`）
- 内存后门 `/_sw_rpc`
- 对外扫描和植入能力

SSH 侧有大量爆破，但**没有发现成功登录**。

主机应按“已失陷”处理，不能只做数据库密码轮换。

---

## 二、根因

`zvp5-home-preview.service` 把 Vite dev server 以 **root** 身份监听在 `0.0.0.0:8080`，没有任何访问控制：

- `User=root`
- `--host 0.0.0.0`
- `--port 8080`
- `--strictPort`
- `NODE_ENV=development`

Vite 的 `import.meta.glob` 选项注入（CVE-2025-30208 同类）可以通过 `Function` 在 node 进程内执行代码。攻击者不需要登录，直接访问 8080 即可利用。

另外：

- `allowedHosts` 只限制了域名，没有阻止直接用 IP 访问。
- 8080 在安全组中开放。
- 服务没有认证、没有来源限制、没有反向代理保护。

---

## 三、攻击链时间线

### 1. 初始入侵

- **11-24 03:34:06**：journal 第一条 `import.meta.glob` 注入报错，攻击开始。
- 攻击载荷形态：

```text
import.meta.glob("/*",[{toString:Function`return Function(globalThis.Buffer.from("...","base64"))...`}]:1})
```

- 报错关键字：`Unknown glob option "RCE_B64:..."`、`Plugin: vite:import-glob`。

### 2. 文件读取

- **11-24 05:31:22**：成功读取 `/root/.codex/installation_id`。
- 之后 **05:53**、**06:02** 又读到同一文件。
- 读取载荷会遍历 home 下的 AI/云凭据目录：

```text
.codex
.openclaw
.openclaw/credentials
.gemini
.claude
.continue
.n8n
.aws
.kube
.deepseek
.qwen
.9router
```

- 读取每个文件的前 2500 字符，输出格式为 `FILE:<path>\n<content>`。
- 这证明攻击者不仅能 dump 环境变量，已经具备**任意文件读取**能力。

### 3. 对外扫描与植入

- **11-24 07:14 / 10:11**：开始出现对外利用结果和 `newRecruits`。
- **11-25 06:52:46**：出现 `{"status":"IMPLANTED_OK","targets":3049}`，扫描器进入正式运行状态。
- 扫描输出包含：
  - `hits`
  - `newRecruits`
  - `pwnedCount`
  - `techCensus`
  - `cveQueue`
  - `liveVite`
  - `slowVite`

### 4. 敏感文件读取尝试

- **11-25 20:02–20:03**：集中尝试读取以下文件，全部被 Vite fs allow list 拦截：

```text
/proc/self/environ
/proc/1/environ
/etc/passwd
/root/.bash_history
/root/.profile
/root/.ssh/config
/root/.ssh/authorized_keys
```

- journal 关键字：`outside of Vite serving allow list`。
- 共 20 条拦截记录。
- 说明攻击者在主动寻找 SSH key、shell 历史和环境变量。

### 5. OOM 与重启

dmesg 记录到 **3 次** OOM kill，不是 2 次：

| 时间 | 被 kill 的进程 | 说明 |
|---|---|---|
| 2026-11-25 10:08:41 | node（preview） | RSS 约 863 MB |
| 2026-11-26 15:31:37 | node（preview） | RSS 约 870 MB |
| 2026-11-27 13:35:02 | node（preview） | RSS 约 1.04 GB；sshd 触发 OOM killer |

补充：

- 11-27 是 `sshd-session invoked oom-killer`，被杀的是 node，不是 sshd。
- 机器只有约 2 GB 内存、无 swap，内存耗尽时 SSH/TLS 会无响应。
- 服务配置了 `Restart=on-failure`，OOM 后 node 会重启，攻击者也会重新植入。

### 6. 环境变量泄露

- **11-28 10:31:39**：成功 dump `process.env`，同一分钟至少 3 次。
- 泄露字段包括：

```text
ZVP5_ORACLE_PASSWORD
ZVP5_ORACLE_WALLET_PASSWORD
ZVP5_SSH_TARGETS
TLS_KEY
CODEX_MANAGED_PACKAGE_ROOT
SYSTEMD_NSS_DYNAMIC_BYPASS
```

- dump 中同时包含 `Platform: linux (x64) | Node: v22.23.3`、`User: root (uid=0)`、`CWD: <PREVIEW_DIR>/releases/...`。
- `ZVP5_SSH_TARGETS` 只泄露目标清单和 key 路径，不含私钥内容。
- `TLS_KEY` 泄露的是路径，不是文件内容。

### 7. 服务停止

- **11-28 10:59:54**：`zvp5-home-preview.service` 停止。
- 8080 不再监听。
- 内存 available 从约 155 MB 恢复到约 1148 MB。

---

## 四、日志量化

### 1. Vite journal

`home-preview-journal.log`：141,630 行，约 28 MB。

| 项目 | 数量 |
|---|---:|
| `import.meta.glob` | 20,243 |
| `RCE_B64` | 25,016 |
| `Unknown glob option` | 6,747 |
| RCE 输出条目 | 3,498 |
| 环境变量 dump | 3 次 |
| 成功文件读取输出 | 1 次（`/root/.codex/installation_id`） |
| 被 Vite 拦截的文件读取 | 20 次 |

按天统计：

| 日期 | `import.meta.glob` | `RCE_B64` | 被拦截读取 |
|---|---:|---:|---:|
| 2026-11-24 | 18,509 | 23,832 | 1 |
| 2026-11-25 | 1,356 | 947 | 16 |
| 2026-11-26 | 171 | 105 | 0 |
| 2026-11-27 | 114 | 74 | 3 |
| 2026-11-28 | 93 | 58 | 0 |

### 2. 扫描结果

从 RCE 输出中解码统计：

| 项目 | 数量 |
|---|---:|
| 唯一公网 IP | 约 4,054 |
| URL | 约 5,251 |
| 命中目标 | 23 |
| `newRecruits` | 2 |
| `pwnedCount` 最高值 | 4 |
| `cveQueue` 条目 | 5,278 |
| `techCensus` 条目 | 415 |

判断：

- 扫描目标全部是公网 IP，没有 RFC1918 内网地址。
- 没有发现扫描你已知基础设施 IP 的记录。
- 说明它主要是对公网批量扫描，不是专门针对你内网的定向攻击。
- 但主机已经被用作攻击跳板，可能触发云厂商滥用告警或第三方投诉。

### 3. 被植入的外部主机

攻击者输出中 `newRecruits` 包含 2 台外部主机（真实 IP 见证据包，本文件用占位符）：

- `<EXTERNAL_VICTIM_1>:5173`
- `<EXTERNAL_VICTIM_2>:5173`

这些是被利用的第三方主机，不是攻击者控制端。部分命中结果里还包含第三方 `.env` / AWS 凭据，说明该 botnet 在批量收集凭据；这些不是本校资产，不要扩散。

---

## 五、SSH 侧结论

`auth-events.log` 共 37,957 行，时间范围 11-21 至 11-28。

注意：该文件是**分段拼接、非时间排序**的，直接按行看会误判时间线；本报告按时间戳重新统计。

### 1. 爆破量

| 项目 | 数量 |
|---|---:|
| 无效用户尝试 | 16,683 |
| root 密码失败 | 3,732 |
| 成功登录 | 730 |
| `Accepted password` | 0 |

主要来源（真实 IP 见证据包）：

- `<SSH_SCANNER_1>`：20,844 次
- `<SSH_SCANNER_2>`：6,477 次
- `<SSH_SCANNER_3>`：5,872 次
- `<SSH_SCANNER_4>`：1,266 次
- `<SSH_SCANNER_5>`：522 次

### 2. 成功登录

成功登录只有两类：

| 用户 | 来源 | 次数 | key |
|---|---|---:|---|
| `root` | `<BACKEND_IP>` | 578 | `debian-new` |
| `github-deploy` | GitHub Actions runner IP | 152 | `github-actions-production-auto-backend` |

结论：

- 没有发现未知用户或未知 key 的成功登录。
- 没有任何密码登录成功。
- 必须确认 `<BACKEND_IP>` 是你的运维/后端出口；如果不是，问题严重。
- 证据包只有该主机的 auth log，没有另一台主机的，无法从本包复核“两机”结论。

---

## 六、README 需要修正的结论

原 `README.md` 方向正确，但有几处低估：

1. **“唯一确认成功的利用是 env dump”不准确。**  
   11-24 05:31 已经成功读取 `/root/.codex/installation_id`；另外还有内存后门 `/_sw_rpc` 和对外扫描/植入。

2. **“日志内无 `/@fs/`、无该路径请求”不准确。**  
   journal 里有 20 条 `outside of Vite serving allow list`，路径包括 `/root/.ssh/authorized_keys`、`/root/.ssh/config`、`/etc/passwd`、`/proc/self/environ` 等。虽然没有 `/@fs/` 字面量，但请求确实发生过，只是被 Vite 拦截。

3. **OOM 是 3 次，不是 2 次。**  
   11-25 10:08 还有一次，README 漏了。

4. **“sshd 本身被 OOM 触发”表述不准确。**  
   dmesg 显示是 `sshd-session invoked oom-killer`，被杀的是 node；sshd 本身没有被杀，但机器内存耗尽导致 SSH/TLS 无响应。

5. **`home-preview-files.sha256` 不是有效清单。**  
   文件内容只有 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`，这是空输入的 SHA-256，说明文件哈希清单实际没有生成。目录清单也只有 10 行，不完整。

6. **`authorized_keys` 的 mtime 是 11-24 04:16，落在攻击窗口内。**  
   指纹仍是 4 把已知 key，但缺少事件前基线。建议和备份/已知 key 列表逐把核对；同时 11-25 20:03 有一次读取 `/root/.ssh/authorized_keys` 的尝试。

7. **证据包只有该主机的 auth log。**  
   README 的“两机 auth.log 无异常”无法从本包复核另一台主机。

8. **`/etc/<OTHER_SERVICE>/origin.key` 的 atime 是 11-27 23:46，落在攻击窗口内。**  
   没有直接读取 payload 证据，但也可能是 另一站点服务正常读取。建议按“可能已泄露”处理，配合服务日志排除。

9. **`/www/zvms4/backend/ssh_key` 不能简单判定“未泄露”。**  
   日志里没有成功读取输出，atime 变化可用取证/部署解释；但攻击者有 root 权限，不能只凭日志排除。建议轮换或至少审查目标机登录日志。

---

## 七、风险判断

### 已确认

- Vite dev server 以 root 身份监听 `0.0.0.0:8080`，无访问控制。
- 攻击者获得 root 代码执行。
- Oracle 数据库密码和 wallet 密码明文泄露。
- SSH 目标清单和 key 路径泄露。
- 服务器被当作 botnet 扫描/植入节点，对外扫描数千个公网目标。
- 内存中安装了 `/_sw_rpc` 后门：hook `http.Server.emit`，接收 deflate-raw 代码并执行。
- 后门重启会消失，但攻击者在 OOM 重启后重新植入过。

### 未确认，但不能排除

- `/www/zvms4/backend/ssh_key` 是否被读取。
- `/etc/<OTHER_SERVICE>/origin.key` 是否被读取。
- 磁盘持久化：现有快照未发现新增 systemd/cron/SUID，`authorized_keys` 指纹未变；但证据不完整。
- 攻击者源 IP：**无法确定**。Vite journal 不含 remote address，证据包也没有 `/tmp/hp.log`（约 20 MB）和 8080 访问日志。

---

## 八、处置清单

### P0：立即

1. 保持 `zvp5-home-preview` 停止并彻底删除。
   - 当前只是 disable，`<PREVIEW_DIR>` 还在。
   - release 目录权限是 `0777`，必须删除或至少修正权限。
2. 在云厂商安全组关闭 8080，不要只靠停服务。
3. 检查出站流量、带宽和云厂商滥用告警。
4. 按“主机已失陷”处理，最稳妥是重建该主机。

### P1：凭据与数据库

1. 确认旧 Oracle 密码已失效，而不只是改了配置。
2. 检查 Oracle ADB 审计日志：
   - 异常登录
   - 异常查询
   - 数据导出
3. 轮换 wallet 密码；如果做不到，至少限制 ADB 网络访问。
4. 轮换 SSH key：
   - `/www/zvms4/backend/ssh_key`
   - `/root/.ssh/authorized_keys` 中无法确认来源的 key
5. 轮换 TLS 证书：
   - `/etc/<OTHER_SERVICE>/origin.key` 按可能泄露处理。

### P2：主机排查

检查以下位置：

```text
/etc/systemd/system
/etc/cron.d
/etc/cron.daily
/var/spool/cron
/tmp
/var/tmp
/etc/ld.so.preload
/root/.ssh/authorized_keys
/root/.ssh/config
/root/.bash_history
/root/.profile
/www/zvms4/backend/ssh_key
/etc/<OTHER_SERVICE>/origin.key
```

重点：

- 新增 systemd unit/timer
- 新增 cron
- 新增 SUID
- 新增 SSH key
- 异常进程
- 异常出站连接

### P3：证据补取

- `/tmp/hp.log`（约 20 MB）
- 8080 访问日志
- nginx access log
- 安全组流日志
- 另一台主机的 auth log

只有补齐这些，才能定位攻击者源 IP 和完整扫描范围。

### P4：证据包清理

- `README.md` 第 48 行仍写着新 Oracle 密码明文。
- `rce-payloads.txt` 里有 base64 编码的旧密码。
- 该目录目前是 **untracked 且没有被 `.gitignore` 忽略**，禁止 `git add -A`。
- 建议移出仓库，或在 `.gitignore` 增加：

```gitignore
<EVIDENCE_DIR>/
```

- 对外分享前必须脱敏。

---

## 九、IOC / 排查关键字

```text
zvp5-home-preview.service
<PREVIEW_DIR>/current
0.0.0.0:8080
import.meta.glob("/*",[{
RCE_B64:
Unknown glob option
outside of Vite serving allow list
/_sw_rpc
newRecruits
pwnedCount
IMPLANTED_OK
COLLECTED_AND_DESTROYED
```

---

## 十、一句话总结

Vite dev server 以 root 暴露在 `0.0.0.0:8080`，被 `import.meta.glob` 注入打成 root RCE；攻击者读取了环境变量和 `/root/.codex/installation_id`，尝试读取 SSH 私钥但被拦截，随后把机器变成 botnet 节点，对外扫描 4,000 多个公网目标并至少植入 2 台主机。SSH 爆破未成功，但主机应按已失陷处理，不能只做密码轮换。
