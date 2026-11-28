# 复盘：Vite dev server 暴露导致主机失陷（2026-11）

- 复盘时间：2026-11-28（主机重装后补全）
- 事件编号：`SEC-2026-11-VITE-RCE`
- 攻击窗口：2026-11-24 03:34:06 — 2026-11-28 10:31:39（**4 天 6 小时 57 分**）
- 服务停止：2026-11-28 10:59:54；主机随后**重装**
- 受影响系统：后端 API（与预览服务同机，2C/2G、无 swap）；前端源站在另一台主机上，仅发布通道受影响

> **脱敏约定**：按 [`zvp5/docs/REDACTION_POLICY.md`](../../zvp5/docs/REDACTION_POLICY.md) 处理。
> 主机只写 `<BACKEND_IP>` / `<FRONTEND_IP>`；不写账号、密钥指纹、第三方受害主机和攻击者源 IP。
> 端口、路径、服务名保留（离开 IP 不构成入口，删掉反而失去可操作性）。真实值只在内部证据包里，
> 证据包不入库、不外发。

---

## 一、结论

一台用于前端预览的 **Vite dev server** 以 **root** 身份监听 `0.0.0.0:8080`，且安全组对外开放。
攻击者无需任何凭据，直接用 `import.meta.glob` 选项注入在 Node 进程内执行代码，取得 root RCE，
随后把主机当作**僵尸网络节点**使用：批量扫描公网、收集凭据、植入其他主机。

主机侧后果：

| 项 | 判断 |
| --- | --- |
| root 代码执行 | **确认** |
| `process.env` 全量泄露 | **确认**（含数据库口令、wallet 口令、SSH 目标清单） |
| 任意文件读取 | **确认**（至少成功读取一处 AI 工具凭据目录下的文件） |
| 内存后门 | **观察到**（`/_sw_rpc`，见 3.5；服务停止后消失，重装后不可复验） |
| 磁盘持久化 | 未发现新增 unit / cron / SUID / `ld.so.preload` |
| SSH 被爆破成功 | **未发现**（成功登录全部来自已知 key） |
| 对外扫描/植入 | **确认**（4 千余个公网目标，至少 2 台被招募） |

处置上按"主机已失陷"处理：**取证 → 归档 → 重装**，而不是清障后继续使用。

---

## 二、根因

### 2.1 直接原因：开发服务器以 root 身份暴露在公网

```ini
# 出问题的 unit（示意）
[Service]
User=root
WorkingDirectory=/opt/zvp5-home-preview/current
Environment=NODE_ENV=development
ExecStart=/usr/bin/node .../vite.js --config <config> --host 0.0.0.0 --port 8080 --strictPort
Restart=on-failure
```

四个条件同时成立：

1. `--host 0.0.0.0` + 安全组放行 8080 → 公网可达；
2. `User=root` → 任何代码执行等价于整机控制；
3. `NODE_ENV=development` → 走 dev 模式，暴露 Vite 转换管线；
4. 没有认证、没有来源限制、没有反向代理保护；`allowedHosts` 只挡域名，不挡直接用 IP 访问。

### 2.2 漏洞机制

Vite 在解析 `import.meta.glob` 时会**对 options 求值**。攻击者构造：

```js
import.meta.glob("/*",{[{toString:Function`return "RCE_B64:"+globalThis.Buffer.from(
  String(Function(...payload...)),"base64")`}]})
```

- options 被求值 → 执行 payload；
- payload 的返回值被包进抛出的错误信息；
- Vite 把错误写进 HTTP 500 页面和 stderr → **这就是命令输出通道**。

日志里的对应特征是 `Unknown glob option "RCE_B64:..."` 与 `Plugin: vite:import-glob`。
同类公开条目：`GHSA-p4f5-76q4-ffq7` / CVE-2025-30208 家族。

### 2.3 为什么"一个预览服务"会变成"整机失守"

| 放大器 | 说明 |
| --- | --- |
| root 身份 | 直接读写 `/root`、`/etc`、systemd 和同机其它服务 |
| 环境变量即凭证库 | `EnvironmentFile` 注入的数据库口令、wallet 口令在 `process.env` 里明文可读 |
| 部署通道 | 部署账号有免密 sudo 规则，可重启/替换应用 |
| 无审计 | 未装 auditd，认证日志只保留当天，写入者无法追溯 |
| 无监控 | 8080 不在任何监控和值班范围内，4 天无人发现 |

---

## 三、攻击链

### 3.1 初始入侵（11-24 03:34:06）

journal 第一条注入即攻击开始：

```
import.meta.glob("/*",{[{toString:Function`return "RCE_B64:"+...
  at parseGlobOptions (.../vite/dist/node/chunks/...)
```

首次返回的状态是 `{"status":"RUNNING"}`，说明载荷一次成功。

### 3.2 任意文件读取（11-24 05:31 起）

- 11-24 05:31:22 成功读取 `/root/.codex/installation_id`，05:53、06:02 重复读取；
- 载荷会遍历 home 下的 AI/云凭据目录（`.codex`、`.claude`、`.aws`、`.kube`、`.gemini` 等），
  每个文件读前 2500 字符，按 `FILE:<path>\n<content>` 回显。

这证明攻击者不仅能 dump 环境变量，已具备**任意文件读取**能力。

### 3.3 敏感文件读取尝试（11-25 20:02–20:03）

集中尝试读取，全部被 Vite 的 fs allow list 拦截（journal 关键字 `outside of Vite serving allow list`，共 20 条）：

```
/proc/self/environ
/proc/1/environ
/etc/passwd
/root/.bash_history
/root/.profile
/root/.ssh/config
/root/.ssh/authorized_keys
```

方向很明确：找 SSH key 和 shell 历史。**被拦下不等于没拿到** —— 攻击者同时具备 root 代码执行，
只是这条特定路径被 Vite 挡了。

### 3.4 对外扫描与植入

- 11-24 07:14 / 10:11 开始出现对外利用结果与 `newRecruits`；
- 11-25 06:52:46 输出 `{"status":"IMPLANTED_OK","targets":3049}`，扫描器进入常态运行；
- 扫描面覆盖 Vite dev server、Next.js dev、Spring Actuator、暴露的 `.env`、OpenWebUI 等，
  端口集中在 `3000/3001/3002/5000/5173/5174/5175/5180/8000/8080`；
- 状态机字段：`hits` / `newRecruits` / `pwnedCount` / `techCensus` / `cveQueue` / `liveVite` / `slowVite`。

### 3.5 内存后门（首轮观察到）

首轮分析记录到 `/_sw_rpc`：hook `http.Server.emit`，接收 deflate-raw 压缩后的代码并执行，
不落盘，**进程重启即消失**。攻击者在每次 OOM 重启后都重新植入过。

> 说明：该结论来自服务停止前的分析记录。进程消失后无法再复验，重装后更不可能复现，
> 因此本文把它列为"当时观察到"，而不是"事后证实"。

### 3.6 环境变量泄露（11-28 10:31:39）

同一分钟内至少 3 次 dump `process.env`，字段包括数据库口令、wallet 口令、SSH 目标清单、
TLS 私钥路径等。回显里还带着 `Platform: linux (x64) | Node: v22.x`、`User: root (uid=0)`、
`CWD: /opt/zvp5-home-preview/releases/...`。

拿到的是**路径**和**明文口令**：能读的都能直接读，因为进程就是 root。

### 3.7 OOM 与重启

2 GB 内存、无 swap，扫描器把内存吃满，dmesg 记录 **3 次** OOM kill：

| 时间 | 被杀进程 | RSS |
| --- | --- | ---: |
| 11-25 10:08:41 | node（预览） | ~863 MB |
| 11-26 15:31:37 | node（预览） | ~870 MB |
| 11-27 13:35:02 | node（预览） | ~1.04 GB |

其中 11-27 是 `sshd-session invoked oom-killer`，被杀的是 node，但机器内存耗尽导致
SSH/TLS 一度无响应。`Restart=on-failure` 让 node 不断重启，攻击者也随之重新植入。

---

## 四、量化

### 4.1 预览服务 journal

| 项目 | 数量 |
| --- | ---: |
| journal 行数 / 体积 | 141,630 行 / ~28 MB |
| `import.meta.glob` | 20,243 |
| `RCE_B64` | 25,016 |
| `Unknown glob option` | 6,747 |
| 完整 RCE 输出条目 | 3,498 |
| 环境变量 dump | 3 |
| 成功的文件读取输出 | 1 |
| 被 Vite 拦截的读取 | 20 |

按天分布（`import.meta.glob` / `RCE_B64` / 被拦截读取）：

| 日期 | glob | RCE_B64 | 拦截 |
| --- | ---: | ---: | ---: |
| 11-24 | 18,509 | 23,832 | 1 |
| 11-25 | 1,356 | 947 | 16 |
| 11-26 | 171 | 105 | 0 |
| 11-27 | 114 | 74 | 3 |
| 11-28 | 93 | 58 | 0 |

> 第一天最凶（注入即扫描），之后转入低频维持，这也是"看起来已经好了"的错觉来源。

### 4.2 扫描结果

| 项目 | 数量 |
| --- | ---: |
| 唯一公网 IP | ~4,054 |
| URL | ~5,251 |
| 命中目标 | 23（去重 URL 22） |
| `newRecruits` | 2 |
| `pwnedCount` 峰值 | 4 |
| `cveQueue` | 5,278 |
| `techCensus` | 415 |

- 扫描目标全部是公网地址，**没有** RFC1918 内网地址；
- **没有**命中我们已知的基础设施 IP；
- 但主机已成为攻击跳板，可能触发云厂商滥用告警或第三方投诉。

### 4.3 SSH 侧

`auth` 日志拼接后 37,957 行（11-21 至 11-28）：

| 项目 | 数量 |
| --- | ---: |
| 无效用户尝试 | 16,683 |
| root 密码失败 | 3,732 |
| 成功登录 | 730 |
| `Accepted password` | **0** |

成功登录只有两类：运维出口 IP 用管理 key（578 次）、CI runner 用部署 key（152 次）。
**没有未知用户、未知 key、密码登录成功。**

另有一路定向爆破使用**组织内人员姓名拼音**作为用户名（例如姓氏「陈」「张」「赵」+ 名），
说明对方掌握人员信息，且在我们停止服务后仍在继续尝试。

---

## 五、应急响应与恢复

| 阶段 | 动作 |
| --- | --- |
| 止损 | 停止预览服务；停用 CI 工作流，阻断任何发布触发部署 |
| 隔离判断 | 按"主机已失陷"处理；不再尝试在原机上清障 |
| 凭证轮换 | 数据库管理员口令重设、wallet 重新下发、应用 secrets 重写、TLS 重新签发、主机 SSH key 重生成 |
| 取证 | 两轮：易失状态 / 进程与内存映射 / 网络 / 持久化 / 内核 / 包完整性 / 时间线；19 个进程的用户态内存 dump；日志专提（原始二进制 journal 覆盖事发前 10 天） |
| 归档 | 单包 586 MB / 492 条目，逐层 SHA256，离线保存 |
| 重建 | **重装系统** → 按归档的实配（nginx / systemd / 数据）恢复 → 服务重新上线 |
| 部署通道重建 | 重新生成前端/后端部署密钥 → 在目标机重建免密部署账号与 4 条受限 sudo → `ssh-keyscan` 重制 `known_hosts` → 更新 GitHub Environment 的 10 个 secret |
| 恢复发布 | 重新启用 CI，走 release 门禁部署，校验静态基址、健康检查与 E2E |

取证结论（重装前）：

- `dpkg -V` 仅 6 个配置文件漂移，**没有二进制被修改**；
- 无新增内核模块（全部带内核构建签名）、无 eBPF、无隐藏进程、无未预期监听；
- 19 个内存 dump 中只有日志守护进程命中载荷字符串（日志缓冲区残留），其余进程无载荷、无反弹 shell 特征；
- **内核态内存无法采集**（`STRICT_DEVMEM`、无 LiME/AVML、`System.map` 是占位文件），
  所以"内核/模块级后门"既不能证实也不能排除 —— 这是最终选择重装而不是清障的直接原因。

---

## 六、为什么能跑 4 天

| 缺口 | 表现 |
| --- | --- |
| 资产归属 | 预览服务不属于任何值班责任域，8080 不在监控里 |
| 行为特征 | 载荷把输出塞进错误信息经日志回显，**几乎不产生出网流量**，网络侧看不到异常 |
| 日志保留 | auth 日志只保留当天；journald 只回溯到事发前 10 天，攻击窗口内的认证记录有缺口 |
| 审计 | 没有 auditd，`authorized_keys` / `sudoers.d` / `/usr/local/sbin` 的写入无法追溯 |
| 惯性 | 服务 `Restart=on-failure`，OOM 后自动重启，攻击者跟着重新植入，"服务可用"掩盖了失陷 |

---

## 七、影响判断

**已确认**

- 预览服务以 root 监听 `0.0.0.0:8080`，无访问控制；
- 攻击者获得 root 代码执行、环境变量读取、任意文件读取；
- 数据库口令、wallet 口令、SSH 目标清单泄露；
- 主机被用作扫描/植入节点（4 千余目标，至少 2 台被招募）。

**未确认，但按已泄露处理**

- 应用部署私钥是否被读取（日志有读取尝试、被 Vite 拦截，但攻击者有 root）；
- 另一服务的 TLS 私钥是否被读取（atime 落在攻击窗口内，无直接读取证据）；
- 数据库侧是否发生异常登录/导出（取证时无审计权限，无法回看攻击窗口）。

**无法确定**

- 攻击者源 IP：Vite journal 不含 remote address，预览服务的访问日志未保留。

---

## 八、改进项

### P0（已完成）

- [x] 受影响主机重装，全部主机侧凭证轮换；
- [x] 部署密钥轮换、`known_hosts` 重新 pin、GitHub secrets 全量更新；
- [x] CI 恢复，release 门禁重新生效。

### P1

- [ ] **禁止任何 dev server 以 root 运行**：`vite --host` 只允许 `127.0.0.1` 或走 SSH 隧道；
      预览站用独立低权账号 + systemd 沙箱（`ProtectSystem=strict`、`PrivateTmp`、`NoNewPrivileges`）；
- [ ] **默认拒绝**：安全组/防火墙不对外开放 8080 这类端口；SSH 只允许 CI 网段与办公出口；
- [ ] 关闭 `PermitRootLogin`、`X11Forwarding`；SSH 加失败限速；
- [ ] 安装 auditd，至少监控 `authorized_keys`、`sudoers.d`、`/usr/local/sbin`；journal 保留 ≥ 30 天；
- [ ] 上线端口/进程/文件完整性监控：**出现 dev server 监听即告警**；
- [ ] 收敛提权链：`www-data → sudo systemctl start <backup>.service` 这类规则换成独立账号，
      或把解释器移出可写目录；
- [ ] 若前端为双 origin 回源（CDN 同时回两台），CI 的 frontend 角色必须覆盖两台，
      或收敛到单一回源，否则发布会造成版本分裂。

### P2

- [ ] 建立凭证清单与轮换周期；
- [ ] 每季度做暴露面巡检：公网监听端口 × 进程属主 × 是否 dev 模式；
- [ ] 预览/内网环境默认走 Access 或 mTLS，不做裸端口暴露；
- [ ] `authorized_keys` 纳入配置管理与基线比对。

---

## 九、给同类项目的检查清单

```bash
# 1. 有没有 root 跑着的开发服务器？
ss -lntp | grep -E ':(3000|3001|3002|5000|5173|5174|5175|5180|8000|8080)\b'
ps -eo user,pid,args | grep -E 'vite|next dev|webpack-dev-server' | grep -v grep

# 2. 有没有可疑的 glob 注入痕迹？
journalctl --since '-7 days' | grep -E 'RCE_B64:|Unknown glob option|import\.meta\.glob\("/\*"'

# 3. authorized_keys 最近被谁改过？
stat -c '%y %n' /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys
ssh-keygen -lf /root/.ssh/authorized_keys

# 4. 有没有被 Vite 拦下的越权读取？
journalctl --since '-7 days' | grep -c 'outside of Vite serving allow list'

# 5. 内存够不够？（2G 无 swap 的机器会被扫描器吃爆）
free -m; dmesg | grep -c 'oom-kill'
```

---

## 十、IOC / 排查关键字

```
zvp5-home-preview.service            # 本项目的失陷入口 unit 名，按自身情况替换
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

## 十一、一句话总结

**一个以 root 身份裸奔在公网上的 Vite dev server，被一行 `import.meta.glob` 打成 root RCE；
攻击者读走环境变量和一处分凭据文件，尝试 SSH 私钥被拦，随后把机器变成僵尸网络节点，
对外扫描 4 千余个公网目标。** 停止服务、轮换全部凭证、重装主机是唯一可靠的收尾方式。
