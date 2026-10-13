# 上线清单 — zvp-frontend ← v4.2.2-1（单库收敛 + changelog 数据库化 + ADB 脚手架）

> 目标机 `zvp-frontend`（root@<FRONTEND_IP>）。生产现为 **v4.2.0-3**，本次升 **v4.2.2-1**。
> 形态：nginx（root `/www/zvms4/frontend-dist`）+ `/api/`、`/socket.io/` → 127.0.0.1:8018；
> systemd `zvms4`（User=www，WorkingDirectory=`/www/zvms4/backend`）；mongo:7 容器 `zvms4-mongo`（127.0.0.1:27017）。

## 0. 前置核对（只读，已完成）

- [x] 当前树本地 e2e **40/40 PASS**。
- [x] 生产数据形状 == 本地 seed：`users` 2566 全有 `roles`；class 组 54 条**无 `type`**；legacy user id 无缺失。
- [x] 迁移 dry-run 精确结果：权限组 **3** + imports **1** + tasks **1**；`groups` 54→57。
- [x] `settings` 读取全程 `getattr` 安全 → prod `settings.py` 缺 `ORACLE_*` 不会崩。
- [x] `changelog_router` → `from db import backends`，故 **`db/` 必须一起部署**。
- [ ] **前端 `dist/` 陈旧**（构建于 22:15，源码 22:25/22:31 之后才改；产物内无 `api/changelog`、无 `v4.2.2-1`）→ **必须重建**。
- [ ] `zvms_new.changelog` 集合**尚不存在** → 需跑 `misc/seed_changelog.py`。

## 1. Phase 0 — 本地准备

```bash
# 1.1 重建前端（当前 dist 陈旧）
cd ~/zvp5-repo/zvms4-modified-main/zvms4-frontend
pnpm install --frozen-lockfile
pnpm build                                   # run-p type-check + build-only
grep -lo 'api/changelog' dist/assets/*.js    # 期望命中
grep -ro 'v4.2.2-1' dist/assets/*.js | head  # 期望命中

# 1.2 后端编译校验
cd ../zvms4-backend-python
.venv/bin/python -m compileall -q . && echo COMPILE_OK
```

## 2. Phase 1 — 备份（生产，先做）

```bash
# 2.1 文件整包备份（沿用 §45 做法）
ssh zvp-frontend 'cd /www/zvms4 && tar czf backups/pre-deploy-$(date +%Y%m%d-%H%M).tar.gz backend frontend-dist'

# 2.2 Mongo 备份（迁移会写库，必须）
TS=$(date +%Y%m%d-%H%M)
ssh zvp-frontend "docker exec zvms4-mongo mongodump --db zvms_new --archive --gzip > /www/zvms4/backups/mongo-zvms_new-$TS.archive.gz"

# 2.3 基线计数（迁移后对照）
ssh zvp-frontend "docker exec zvms4-mongo mongosh --quiet --eval 'const d=db.getSiblingDB(\"zvms_new\"); print(\"groups=\"+d.groups.countDocuments({})+\" perm=\"+d.groups.countDocuments({type:\"permission\"})+\" imports=\"+d.imports.countDocuments({})+\" tasks=\"+d.tasks.countDocuments({}))'"
# 期望：groups=54 perm=0 imports=0 tasks=0
```

## 3. Phase 2 — 同步后端（服务运行中，无 `--reload`，替换文件不影响在跑进程）

```bash
# 3.1 rsync（排除本地 settings / venv / 密钥 / pyc）
rsync -az \
  --exclude settings.py --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude aes_key.txt --exclude 'rsa_*.pem' \
  ~/zvp5-repo/zvms4-modified-main/zvms4-backend-python/ zvp-frontend:/www/zvms4/backend/

# 3.2 归属（rsync 以 root 写，须改回 www）
ssh zvp-frontend 'chown -R www:www /www/zvms4/backend'

# 3.3 依赖（新增 sqlalchemy[asyncio]==2.0.* 与 oracledb>=2.0，其余逐行一致）
ssh zvp-frontend '/www/zvms4/backend/.venv/bin/pip install -r /www/zvms4/backend/requirements.txt'

# 3.4（可选，非必需）prod settings.py 追加 ORACLE_* 块，ORACLE_ENABLED=False —— 仅为后续 ADB 预留
```

## 4. Phase 3 — 前端 dist

```bash
rsync -az --delete ~/zvp5-repo/zvms4-modified-main/zvms4-frontend/dist/ zvp-frontend:/www/zvms4/frontend-dist/
ssh zvp-frontend 'chown -R www:www /www/zvms4/frontend-dist'
```

## 5. Phase 4 — 迁移（停机窗口，秒级）

> 顺序：**先停服再迁移**，避免旧代码（zvms_new class + legacy permission）在迁移后把权限组列两遍。

```bash
# 5.1 停服
ssh zvp-frontend 'systemctl stop zvms4'

# 5.2 dry-run 复核 → 执行（幂等）
ssh zvp-frontend 'cd /www/zvms4/backend && .venv/bin/python misc/migrate_legacy_to_new.py --dry-run'
ssh zvp-frontend 'cd /www/zvms4/backend && .venv/bin/python misc/migrate_legacy_to_new.py'

# 5.3 changelog 入库（zvms_new.changelog 尚不存在，幂等）
ssh zvp-frontend 'cd /www/zvms4/backend && .venv/bin/python misc/seed_changelog.py'

# 5.4 计数校验（期望 groups=57 perm=3 imports=1 tasks=1；changelog=3）

# 5.5 起服
ssh zvp-frontend 'systemctl start zvms4'
```

## 6. Phase 5 — 部署验证

```bash
# shield：无 Mozilla UA → 418
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8018/api/health
# 带 UA → 200；版本 = v4.2.2-1
curl -s -A 'Mozilla/5.0' http://127.0.0.1:8018/api/version
# 权限组（admin token）GET /api/v2/groups → 含 团支书/学生会/管理员
# changelog 公开 GET /api/changelog → 3 条
# nginx 全链路：index 200 + 引用新 assets hash
ssh zvp-frontend 'journalctl -u zvms4 -n 30 --no-pager | grep -iE "error|traceback|startup" '
```

- [ ] `journalctl` 无异常，`Application startup complete`。
- [ ] `/api/version` = `v4.2.2-1`。
- [ ] 权限组 3 个出现在群组管理页与个人身份。
- [ ] `/changelog` 页面正常（读库，3 条）。
- [ ] 浏览器实测登录 + 用户/班级/义工/日志页无 5xx。

## 7. Phase 6 — 回滚

```bash
ssh zvp-frontend 'cd /www/zvms4 && tar xzf backups/pre-deploy-<ts>.tar.gz -C /'
# 如需回滚数据：
ssh zvp-frontend "docker exec -i zvms4-mongo mongorestore --archive --gzip --drop < /www/zvms4/backups/mongo-zvms_new-<ts>.archive.gz"
ssh zvp-frontend 'systemctl restart zvms4'
```

## 8. 已知影响 / 注意

- **历史日志不可见**：legacy `zvms.logs`=97605 + 旧 `zvms_new.logs`=7968 不进新日志页（新代码写 `app_logs`，从零开始）——2026-10-12 既定决策。
- **JWT/RSA 密钥不变**（本次不重生成）→ 用户**无需重新登录**（区别于 §45）。
- **ADB 惰性**：`ORACLE_ENABLED=False`，`sqlalchemy`/`oracledb` 运行期不导入；无 wallet/Oracle 依赖。`db/` 内 Oracle 实现与 `db/etl/` 均为死代码。
- 版本跳变 `v4.2.0-3 → v4.2.2-1`（含 Part 11 前端批次 + Part 12 changelog 数据库化 + 单库收敛）。
