# ADB 手动备份栈（adb-sync）

后端 `POST /api/system/db/backup/run` 只负责**触发**：生产写
`/www/zvms4/backend/data/adb-sync.request`，root 的 `adb-sync-trigger.path` 看到后
启动 oneshot 单元；真正的 prod → adb2 全量同步由 `sync_adb.py` 在 root 下执行
（zvp-backend 实测约 35–40 秒；冷缓存可能更久）。本地/旧部署没有 path 单元时回落到
`sudo -n systemctl start --no-block adb-sync.service`。
进度由 `GET /api/system/db/backup/progress` 读 `/var/log/adb-sync.log` 尾部解析。

本目录是这套栈的**部署源**。密钥/钱包**不在仓库里**，必须从旧机或备份迁移。

## 文件

| 文件 | 落点 | 说明 |
|---|---|---|
| `sync_adb.py` | `/root/adb-backup/sync_adb.py` | 同步脚本；`ADB_SYNC_DRY_RUN=1` 只读预演 |
| `adb-sync.service` | `/etc/systemd/system/adb-sync.service` | oneshot；`ExecStart` 用后端 venv 的 python |
| `adb-sync-trigger.path` | `/etc/systemd/system/adb-sync-trigger.path` | 监听后端写的请求文件（生产触发主通道） |
| `adb-sync-trigger.service` | `/etc/systemd/system/adb-sync-trigger.service` | 删请求文件并启动 `adb-sync.service` |
| `adb-sync.sudoers` | `/etc/sudoers.d/adb-sync` | 旧部署/无 path 单元时的兼容触发 |
| `install.sh` | 在目标机执行 | 安装 unit/sudoers/cron 并修正权限 |

## 首次部署 / 主机迁移

```bash
# 1) 从旧机搬运行目录（含 wallet1 / wallet2 / admin_pw1 / admin_pw2）
ssh zvp-frontend 'tar -C /root -cf - \
  --exclude="__pycache__" --exclude="snapshot_*" --exclude="*.log" adb-backup' \
  | ssh zvp-backend 'tar -C /root -xf -'

# 2) 安装单元与触发白名单
ssh zvp-backend 'bash /root/adb-backup/install.sh'

# 3) 只读预演（确认能连上 prod/adb2，不写目标库）
ssh zvp-backend 'cd /root/adb-backup && \
  ADB_SYNC_DRY_RUN=1 /www/zvms4/backend/.venv/bin/python sync_adb.py'

# 4) 真实跑一次（约 35–40 秒；完成看 SYNC_OK / SYNC_FAIL）
ssh zvp-backend 'sudo systemctl start --no-block adb-sync.service'
ssh zvp-backend 'tail -f /var/log/adb-sync.log'
```

## 为什么不能再依赖 systemd 之外的路径

生产的 `zvms4.service` 带 `NoNewPrivileges=true`：**这个进程里 sudo 一定失败**
（日志是 `sudo: If sudo is running in a container, you may need to adjust ...`）。
所以手动触发改成「后端写 `/www/zvms4/backend/data/adb-sync.request` → root 的
`adb-sync-trigger.path` 看到 → 启动 `adb-sync.service` 并删掉请求文件」。
提权动作由 systemd 完成，后端进程仍然没有任何提权能力。

`services/adb_backup.py` 的判定顺序：

1. 有 `adb-sync-trigger.path` 且请求文件父目录可写 → 写请求文件；
2. 否则回落到 `sudo -n systemctl start`（本地/旧部署）；
3. 单元不存在时 `systemctl` 返回非零，服务层翻译成
   `备份服务未安装：目标主机缺少 adb-sync.service`（`NOT_INSTALLED_MESSAGE`）。

## 权限

- `/root/adb-backup` 及两个 wallet 目录 `700 root:root`；
- `admin_pw1` / `admin_pw2` 与 wallet 文件 `600 root:root`；
- `/var/log/adb-sync.log` `640 root:www-data`（后端以 `www-data` 读日志）；
- `/etc/sudoers.d/adb-sync` `440 root:root`，改完必须 `visudo -cf`。

## 定时

`install.sh` 会写 `/etc/cron.d/adb-sync`：每天 03:30 由 root 触发一次。
它与手动触发共用同一个单元，`sync_adb.py` 内部还有 `flock`，重复触发只会打印
`another sync instance running, exit`。

## 故障排查

| 现象 | 检查 |
|---|---|
| 后端 500「备份服务未安装」 | `systemctl cat adb-sync.service`、`ls /etc/sudoers.d/adb-sync` |
| 后端 500「启动备份任务失败：sudo: a password is required」 | sudoers 的 user 必须是 `www-data`（不是 `www`） |
| 日志里 `SYNC_FAIL` | 看紧邻的 ORA- 错误；`ADB_SYNC_DRY_RUN=1` 复现读路径 |
| 进度一直 `running` | `systemctl is-active adb-sync`（oneshot 运行期是 `activating`） |
