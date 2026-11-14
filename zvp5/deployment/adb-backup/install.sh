#!/bin/bash
# 安装 ADB 手动备份栈（幂等）。需要 root。
#
# 前置：/root/adb-backup 已含 sync_adb.py、wallet1、wallet2、admin_pw1、admin_pw2。
# 本脚本只做「把单元/白名单/定时/权限放到正确位置」，不搬运密钥。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR=/root/adb-backup
VENV_PY=${ZVP5_VENV_PY:-${ZVMS_VENV_PY:-/www/zvp5/backend/.venv/bin/python}}
SERVICE_USER=${ZVP5_SERVICE_USER:-${ZVMS_SERVICE_USER:-www-data}}

[[ $EUID -eq 0 ]] || { echo "must run as root" >&2; exit 1; }
[[ -x "$VENV_PY" ]] || { echo "python not found: $VENV_PY" >&2; exit 1; }

install -d -m 700 "$BACKUP_DIR"
chown -R root:root "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" "$BACKUP_DIR/wallet1" "$BACKUP_DIR/wallet2"
chmod 600 "$BACKUP_DIR/admin_pw1" "$BACKUP_DIR/admin_pw2"
find "$BACKUP_DIR/wallet1" "$BACKUP_DIR/wallet2" -type f -exec chmod 600 {} +

# 请求文件目录：后端（www-data）写，root 的 path 单元看。
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" /www/zvp5/backend/data

# 单元里的 python 路径按部署实际值替换（仓库模板是 /www/zvp5/backend/.venv）。
sed "s#^ExecStart=.*#ExecStart=$VENV_PY $BACKUP_DIR/sync_adb.py#" \
  "$HERE/adb-sync.service" > /etc/systemd/system/adb-sync.service
chmod 644 /etc/systemd/system/adb-sync.service

sed "s#^www-data#${SERVICE_USER}#" "$HERE/adb-sync.sudoers" \
  > /etc/sudoers.d/adb-sync
chown root:root /etc/sudoers.d/adb-sync
chmod 440 /etc/sudoers.d/adb-sync
visudo -cf /etc/sudoers.d/adb-sync

install -m 640 -o root -g "$SERVICE_USER" /dev/null /var/log/adb-sync.log
install -m 644 "$HERE/adb-sync-trigger.path" /etc/systemd/system/adb-sync-trigger.path
install -m 644 "$HERE/adb-sync-trigger.service" /etc/systemd/system/adb-sync-trigger.service
printf '30 3 * * * root /usr/bin/systemctl start --no-block adb-sync.service\n' \
  > /etc/cron.d/adb-sync
chmod 644 /etc/cron.d/adb-sync

systemctl daemon-reload
systemctl enable --now adb-sync-trigger.path
echo "installed: adb-sync.service + adb-sync-trigger.path (ExecStart=$VENV_PY)"
echo "next: ADB_SYNC_DRY_RUN=1 $VENV_PY $BACKUP_DIR/sync_adb.py"
