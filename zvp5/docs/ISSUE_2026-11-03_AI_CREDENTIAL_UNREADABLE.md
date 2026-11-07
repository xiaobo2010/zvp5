# 事故：AI 网关全线 `AI_CREDENTIAL_UNREADABLE`（2026-11-03）

> 症状：Web SSH 的「AI 生成命令」（`POST /api/system/ssh/suggest`，purpose
> `system-ssh-suggest`）显示「AI 生成失败 / 凭据无法读取」；后台流水里的错误码是
> `AI_CREDENTIAL_UNREADABLE`。同一时刻库里的 8 条 AI 凭据与 4 条 OCR 引擎凭证
> 全部解不开。

## 1. 结论（先给答案）

**不是网关逻辑的 bug，是部署时换了主密钥。** 新主机 tencent-43 上的
`aes_key.txt` 不是当初加密这些凭据的那一把：库里 12 条密文用**旧 key** 全部可解，
用**现网 key** 全部报 `credential cannot be decrypted`。网关只是如实把
`services/ai/credentials.py:625 resolve_api_key` 的解密失败翻译成 502。

## 2. 证据链

### 2.1 用户可见的失败面

`AI_CALL_LOGS` 里 `AI_CREDENTIAL_UNREADABLE` 只出现在一个 purpose 上：

```
2026-11-03 17:31:41  AI_CREDENTIAL_UNREADABLE  system-ssh-suggest  openai-gpt
2026-11-03 17:31:50  AI_CREDENTIAL_UNREADABLE  system-ssh-suggest  siliconflow
2026-11-03 17:31:53  AI_CREDENTIAL_UNREADABLE  system-ssh-suggest  zhipu
```

其余面（agent / chat）没有该错误，是因为 10-31 之后没人再触发过；数据层是同一
份，任何入口都会中招。10-30 16:39–17:33、10-31 12:34–12:42 的 agent 调用还是
`success`——那批请求由**旧 key 仍在内存/旧主机**的进程服务。

### 2.2 密文可解性实测（只读）

在 tencent-43 用后端 venv 直接调 `services.ai.crypto.decrypt_secret`：

| 表 | 行数 | 现网 key 解密 |
|---|---|---|
| `AI_CREDENTIALS` | 8 | **8/8 UNREADABLE** |
| `AI_OCR_ENGINES` | 5（4 条有密文） | **4/4 UNREADABLE** |

同样的 12 条密文拿到本仓库，用仓库根的 `aes_key.txt` 解密：**12/12 成功**
（AI key 能取出明文长度与末四位，OCR secret 能解析出 `api_key` / `secret_id` /
`secret_key` 等字段）。两把 key 的 SHA-256 不同，内容确实被换过。

### 2.3 换 key 的时间与来源

tencent-43 上三个文件的时间戳完全一致：

```
2026-10-30 22:26  /www/zvms4/backend/.venv          （新 venv）
2026-10-30 22:28  /www/zvms4/backend/aes_key.txt    （64 字节，无换行）
2026-10-30 22:28  /www/zvms4/backend/rsa_private_key.pem
2026-10-30 22:28  /www/zvms4/backend/rsa_public_key.pem
```

64 字节、无换行正是 `tools/setup_local.py` 的
`secrets.token_hex(32)` 写法（`generate_key.sh` 走 `openssl rand -hex 32`，会多
一个换行、65 字节）。RSA 与 AES 同分钟重建、venv 刚建好——**部署时密钥文件没有
随包迁移，有人在生产目录跑了「本地开发引导」脚本，脚本就地生成了一套新密钥。**

部署文档只强调了 rsync 要 `--exclude aes_key.txt`（防止本地覆盖生产），但没写
「新主机首次部署要先从旧机/备份把密钥搬过来」。rsync 的 exclude 只保证不覆盖，
不保证目标端存在；目标端缺失时，`setup_local.py` 会安静地补一把新的。

## 3. 为什么网关代码看起来「没错」

`resolve_api_key()` 的设计是「解不开就 502，不拿乱码去调上游」：

- `services/ai/credentials.py:625-654`：`server` 项读 env；`personal`/`shared` 项
  `crypto.decrypt_secret()`，失败抛 `CredentialError("AI_CREDENTIAL_UNREADABLE", …, 502)`。
- `services/ai/crypto.py:74-96`：GCM 自带完整性校验，换 key / 密文被改都会抛同一个
  `ValueError`，这是刻意的——不区分「哪把 key」是为了不给离线爆破提供信号。
- 各入口（`routers/ai_router.py:2218/2469`、`services/ai/textgen.py:328`、
  `services/agent/executor.py:282`、`services/ai/safety_guard.py:226`）都把该错误
  原样翻译成 502 `detail=AI_CREDENTIAL_UNREADABLE`，前端再翻成「凭据无法读取，
  请管理员重新录入」。

也就是说：**协议、错误码、前端文案都是对的；错的是密钥材料。** 真正该补的是
「密钥不能在生产机上被静默重建」这条防线。

## 4. 修复

### 4.1 代码侧（已落地）

`tools/setup_local.py` 增加生产护栏：

- `settings.py` 里 `ORACLE_ACTIVE = "prod"` 且三个密钥文件不齐时，**拒绝生成**并
  退出（退出码非 0），提示从备份迁移 `aes_key.txt` / `rsa_*.pem`；
- 三个文件齐全时照常空跑（不误伤「只是补 settings」的场景）；
- 确需轮换时显式设 `ZVMS_ALLOW_KEY_BOOTSTRAP=1` 放行；
- 回归用例：`tests/test_setup_local_guard.py`（4 条）。

### 4.2 数据侧（已执行：保留现网 key + 重加密）

采用「保留 10-30 新生成的现网 key，把 12 条密文从旧 key 重加密过来」，恢复生产与
开发仓库的密钥隔离。执行记录（2026-11-03 22:22）：

1. 旧 key（与密文匹配的那把）以 `600 root` 临时放到 `/root`，脚本以 root 身份跑
   （root 可直接读 wallet，`www-data` 读不到临时 key）；
2. `--dry-run`：`planned=12 problems=0`，12 条全部「旧 key 可解 + 新 key 回环一致」；
3. `--apply`：先写密文备份 `/www/zvms4/backups/ai-cipher-backup-20261103-222233.json`
   （600，只有密文，可安全留存），再逐行
   `UPDATE ... WHERE oid = :oid AND <cipher_col> = :old`（乐观锁），12 行 `rowcount=1`，
   一次提交；
4. 清 Redis `ai:cred:*`（本次为空，无需处理）；OCR 引擎每次读库、无缓存；
5. 删除 `/root` 下的临时旧 key 与脚本（旧 key 仍只存在于开发仓库，作为备份解密
   材料；备份文件本身不含密钥）。

### 4.3 验证（已通过）

服务层按真实网关路径跑了一遍（`credentials.catalog(user_oid)` → `resolve_api_key`）：

```
catalog entries: 6
personal/cloudflare  resolve OK len=53
personal/dasein-gpt  resolve OK len=67
personal/deepseek-hyr resolve OK len=35
shared/openai-gpt    resolve OK len=67
shared/siliconflow   resolve OK len=51  → upstream /models OK: 99 models
shared/zhipu         resolve OK len=49  → upstream /models OK: 12 models
```

另外逐行核对 8 条 AI 凭据的明文末四位与库里的 `key_hint` 全部一致；4 条 OCR 凭证
解出的字段 `api_key` / `secret_id` / `secret_key` / `region` 与录入形状一致。
`resolve_api_key` 全程没有再抛 `CredentialError`。

> 复验命令（只读）：按本文件 §4.2 的脚本重跑 `--dry-run` 会因旧 key 已删除而报
> 「cannot decrypt with old key」，这是预期的——此时应改用现网 key 直接解密验证。

## 5. 防复发清单

1. **新主机部署前置**：先把旧机 `aes_key.txt` / `rsa_*.pem` 落到目标目录并
   `chown www-data:www-data && chmod 600`，再起服务；rsync 的 `--exclude` 不是迁移。
2. `tools/setup_local.py` 生产护栏（本次已加）——生产缺密钥应当启动失败，而不是
   悄悄换锁。
3. 若确实要轮换主密钥：先跑「旧 key → 新 key」重加密（AI 凭据 + OCR 引擎），
   再换文件；`settings.example.py:116-121` 已写明「换 key 等于旧密文全废」。
4. 建议给 `/api/ai/health` 增加「可解密条目数」布尔统计（不泄露密钥），让这类
   事故在用户点按钮之前就可见——本次未改，属可选的观测性增强。

## 6. 主密钥落点改造（LoadCredential，2026-11-03 已部署）

目标：密钥不再进入部署树，rsync/重建再也碰不到它。

- 新增 `util/platform_key.py`，统一解析顺序：
  `ZVMS_PLATFORM_KEY_FILE` → `$CREDENTIALS_DIRECTORY/zvms_ai_key` → 旧
  `aes_key.txt`；`CREDENTIALS_DIRECTORY` 存在但文件缺失时**启动失败**，不静默回退。
- `util/cert.py`（JWT/TOTP）、`util/attachment.py`（附件签名）、
  `services/ai/crypto.py`（AI 凭据）三处读取点全部收敛到该模块。
  **原始文本不 strip**：cert/attachment 用原始字节派生，crypto 自己 strip——
  统一 strip 会改变既有 TOTP 与附件签名。
- `services/file_ops.py` 的凭据黑名单补 `ai-key`。
- `deployment/zvms4.service` 增加
  `LoadCredential=zvms_ai_key:/etc/zvms4/ai-key`。

部署步骤（2026-11-03 23:03–23:05 已执行）：

```bash
# 1) 密钥搬到部署树外（root 读给 systemd，服务本身不直接读源文件）
ssh tencent-43 'install -D -m 600 -o root -g root /www/zvms4/backend/aes_key.txt /etc/zvms4/ai-key'
# 2) 更新 unit（保留 10-ssh-targets.conf drop-in）
scp deployment/zvms4.service tencent-43:/etc/systemd/system/zvms4.service
ssh tencent-43 'systemctl daemon-reload && systemctl restart zvms4'
# 3) 验证四条链路：AI 对话 / TOTP 双人码 / JWT 登录 / 附件签名下载
# 4) 稳定一周后删旧文件
ssh tencent-43 'rm -f /www/zvms4/backend/aes_key.txt'
```

部署验证（tencent-43）：

```
resolve  /run/credentials/zvms4.service/zvms_ai_key
crypto   与旧文件派生结果一致
sign     与旧文件派生结果一致
catalog  6 entries，resolve_api_key 6/6 OK
```

`read_platform_key` 对 systemd 凭据副本跳过 chmod 自检（`/run/credentials` 只读、
权限由 systemd 管理），否则每次启动会留下「权限过宽且无法收紧」的假告警。

回退：删掉 unit 里的 `LoadCredential` 行 → `daemon-reload` → 重启；旧
`aes_key.txt` 在观察期内不要提前删，兼容回退会自动命中。
