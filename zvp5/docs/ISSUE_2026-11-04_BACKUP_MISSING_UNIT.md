# 事故/修复：手动备份失败不是 AES，是主机迁移漏了 adb-sync 单元（2026-11-04）

> 症状：后台「数据库备份」点“立即备份”返回 500「备份任务执行失败」，`GET
> /api/system/db/backup/progress` 长期 `idle`。用户怀疑是 10-30 的 AES 主密钥改造。

## 1. 结论

**与 AES 无关。** AES 改造（`util/platform_key.py` + systemd `LoadCredential`）只影响
AI 凭据 / OCR 凭据 / TOTP / 附件签名的解密，备份链路一个字节都不读它。

真正原因：后端从 `zgovps-la` 迁到 `tencent-43` 时，**只迁了应用，没有迁备份栈**。
`tencent-43` 上：

```
$ systemctl cat adb-sync.service   → No files found
$ ls /var/log/adb-sync.log          → No such file or directory
$ ls /root/adb-backup              → No such file or directory
```

而后端 `services/adb_backup.trigger_backup()` 固定执行
`sudo -n /usr/bin/systemctl start --no-block adb-sync.service`，单元不存在 → 非零退出
→ `BackupTriggerError` → 路由翻成 500。

旧机 `zgovps-la` 上这套栈是完整的（单元、sudoers、cron、脚本、wallet、日志都在，最近
一次成功是 2026-11-04 03:30），所以「以前能点」与「现在不能点」的差异就是主机迁移。

## 2. 修复（已执行）

1. 把 `/root/adb-backup` 从 `zgovps-la` 整体搬到 `tencent-43`（含两个 wallet 与密码文件，
   目录 700 / 文件 600 root:root）；
2. 安装 `adb-sync.service`，`ExecStart` 改用后端 venv 的 python
   （`/www/zvms4/backend/.venv/bin/python`，oracledb 26.1.0）；
3. `/etc/sudoers.d/adb-sync` 放行 **`www-data`**（不是旧机的 `www`）执行那条
   `systemctl start`；
4. `/var/log/adb-sync.log` 建为 `640 root:www-data`（后端以 `www-data` 读）；
5. `/etc/cron.d/adb-sync` 每天 03:30 触发；
6. 用与后端完全相同的路径触发一次真实备份。

## 3. 证据

```
# 触发（www-data → sudoers → systemctl）
$ sudo -u www-data sudo -n /usr/bin/systemctl start --no-block adb-sync.service
$ systemctl is-active adb-sync.service
activating

# 日志（已脱敏：不含 DSN）
2026-11-04T22:22:41 OK connected src=... dst=... schema=ADMIN
2026-11-04T22:23:15 COMMIT ok (34.4s)
2026-11-04T22:23:18 SYNC_OK tables=51 rows=41724 elapsed=37.7s
```

后端视角（同一份快照解析逻辑）：

```json
{"state":"ok","started_at":"2026-11-04T22:22:41","finished_at":"2026-11-04T22:23:18",
 "tables_total":51,"tables_done":51,"rows_loaded":41724,
 "elapsed_seconds":37.7,"message":"最近一次备份成功（51/51 张表、41724 行，用时 37.7 秒）"}
```

## 4. 代码侧改进

`services/adb_backup.py` 新增 `NOT_INSTALLED_MESSAGE`：`systemctl` 返回
`Unit ... could not be found` 时翻译成「备份服务未安装：目标主机缺少 adb-sync.service」，
不再让管理员对着泛泛的「启动备份任务失败」猜密钥/权限。回归用例：
`tests/test_adb_backup.py::test_trigger_missing_unit_is_reported_as_not_installed`。

部署源固化在 `deployment/adb-backup/`（`sync_adb.py` / 单元 / sudoers / `install.sh` /
README），下次迁移按 README 执行，不再依赖“旧机上还有一份”。

## 5. 防复发

- 主机迁移清单加一条：`systemctl cat adb-sync.service && ls /var/log/adb-sync.log`；
- 后端 500 的审计摘要现在会直接写「未安装」，无需登录服务器才能定位；
- 备份栈的密钥/钱包**永远不进仓库**，只进 `deployment/adb-backup/README.md` 的迁移步骤。

## 6. 第二个坑：`NoNewPrivileges=true` 让 sudo 必然失败（同日修复）

把单元搬过去后，HTTP 触发仍然 500，审计里是：

```
启动备份任务失败：sudo: If sudo is running in a container,
you may need to adjust the container configuration to disable the flag.
```

根因不是 sudoers 写错，而是 `zvms4.service` 的 `NoNewPrivileges=true`：这个进程
里的 setuid sudo **不可能**提权。旧机没有这个加固项，所以「同样的代码」在旧机
能跑、新机不能。

没有为了备份去掉 NNP（那会把整个 API 进程的提权面打开），而是把触发降级成文件：

- 后端写 `/www/zvms4/backend/data/adb-sync.request`（www-data 可写）；
- root 的 `adb-sync-trigger.path`（`PathExists=`）看到文件 → 启动
  `adb-sync-trigger.service` → 删文件并 `systemctl start --no-block adb-sync.service`。

`services/adb_backup.trigger_backup()` 的判定：有 path 单元且目录可写 → 写文件；
否则回落到原来的 sudo 路径（本地/旧部署）。已安装并 `enable --now`。

实测（HTTP，与前端同一条路径）：

```
trigger 200
progress 200 running 6/51 ... 39/51 ... 48/51
progress 200 ok 51/51 最近一次备份成功（51/51 张表、41742 行，用时 34.9 秒）
```

回归：`tests/test_adb_backup.py` 增加「有 path 单元时绝不调用 sudo」「没有 path
单元时回落 sudo」两条。
