# ZVP Modified v4

> 基于上游 **zvms4**（`zvms/zvms4-backend-python` + `zvms/zvms4-frontend`）的定制版本。
> **改动方向一：数据库层向现役 zvp 平台的真实结构对齐。**
> **改动方向二（2026-10-09 起）：义工管理模块（创建义工 / 添加修改义工成员）按指令从现役 zvp 移植，经逐文件安全审计放行，见 `STATUS.md`。**
> 其余前端 UI 与后端业务逻辑仍以上游 zvms4 为基准，不采用 zvp 的代码（zvp 代码经检查存在漏洞与后门，仅提取其数据结构；义工管理移植面经扫描确认无外链 / 外呼 / 越权代码，范围外危险代码一律未引入）。

---

## 1. 目录结构

```
zvp-modified-v4/
├── README.md                 # 本文件
├── STATUS.md                 # 义工管理移植：端点对齐 / 安全审计 / 验证记录（2026-10-09）
├── DB_ALIGNMENT.md           # 现役数据库真实结构（Ground Truth）+ 对齐改动清单
├── zvms4-backend-python/     # FastAPI 后端（上游 zvms4，DB 层已对齐；义工管理 v3 已按 zvp 移植）
│   ├── README.md
│   ├── requirements.txt
│   ├── config/               # supervisor.conf / nginx 样例
│   └── ...
└── zvms4-frontend/           # Vue3 + Vite 前端（上游为基准；仅义工管理 API 接线改动，见 STATUS.md §2）
```

> **入库路径**：本目录在 GitHub 仓库 `xiaobo2010/zvp5` 中对应 `zvms4-modified-main/`（本地目录名 `zvp-modified-v4` 与入库目录名不同，同步时注意映射）。

## 2. 这份代码从哪来、为什么

| 部分 | 来源 | 说明 |
|---|---|---|
| 前端 | 上游 zvms4 为基准 + zvp 接线 | 仅义工管理 API 接线改动（`member.ts` / `read.ts` / `getActivity.ts` / `ZActivityMemberList.vue`），UI 与模板未动 |
| 后端逻辑 | 上游 zvms4 为基准 | 除下述义工管理移植面外，业务逻辑、路由、权限均保持 zvms4 |
| 义工管理 v3 层 | **现役 zvp**（2026-10-09 移植） | `routers/activities_v3_router.py` 四端点 + 权限修复，逐文件安全审计后放行，见 `STATUS.md` |
| 后端数据库层 | 向**现役 zvp** 对齐 | 改动见 §4 |

**zvp（现役平台）贡献「数据在库里长什么样」**：包名、集合名、字段名 / 类型。除经安全审计放行的义工管理移植面外，它的代码（含后门）一律不引入。

## 3. 现役数据库结构（权威依据）

现役 zvp 连接的是 **MongoDB Atlas 远程集群**，使用两个库：

- **`zvms`**（遗留库）：`users`(`id,name,sex,group[],password,past`)、`groups`(`name,description,permissions[],type`)、`tasks`、`logs`、`imports`、`notifications`；`activities` 为空。
- **`zvms_new`**（现役主库）：`users`(`id,name,roles[],class_id,password,session_id`)、`groups`(`name`)、`activities`、`activity_members`、`time`、`time_academic_year`、`time_with_origin`、`time_with_origin_stat`、`indicators`、`group_indicators`、`daily_exports`、`logs`。

完整字段 / 类型 / 文档数、以及「哪些端点用哪个库」的规律，见 **`DB_ALIGNMENT.md`**（由 SSH 只读 dump 建立，是目前唯一权威依据）。

## 4. 本次对齐做了什么

**规则**：v2 / v3 与 exports / imports、登录鉴权路径统一改用 `zvms_new` 的新 schema；v1（用户列表、班级 v1）与 `util/*` 保持遗留 `zvms.*` —— 与现役 zvp 的集合归属完全一致。

- **查询层**：5 个文件 11 处集合目标切换，含字段映射（旧 `user["group"]/sex/past` → 新 `roles/class_id`）。逐条见 `DB_ALIGNMENT.md` §5。
  - `util/cert.py`（登录鉴权读/写切到 `zvms_new.users`，权限取 `roles`）
  - `routers/users_router.py`（改密端点）
  - `routers/users_v2_router.py`
  - `routers/exports_router.py`（用户/班级来源切换，班级按 `class_id`）
  - `routers/imports_router.py`
- **模型层**：`typings/group.py` 的 `UserPosition` 枚举删去实库与 zvp 均不存在的 `auditor/system/inspector`。
  - `typings/activity_v2.py` **保持上游严格版本**（`datetime` + 非空）：实库 `activities` 的日期为 BSON `Date` 且零 null，若改用 zvp 的 `int`/可空版本会让 `Activity.model_validate` 直接失败。见 `DB_ALIGNMENT.md` §6。
- **未改动**：`database.py`（与 zvp 逐字节相同，已连两库）、其余业务逻辑。前端本次改动仅为义工管理 API 接线（见 `STATUS.md` §2），模板 / 样式未动。

验证：`python3 -m compileall zvms4-backend-python` 通过；改动集中在上述 6 个文件。

## 5. 配置与运行

### 5.1 配置

```bash
cp zvms4-backend-python/settings.example.py zvms4-backend-python/settings.py
```

`settings.py` 填写：

- `MONGODB_URI`：指向现役数据所在的 MongoDB（当前为 Atlas 集群）。
- `MONGODB_DB`：按上游约定留空即可；`database.py` 固定连接 `zvms` 与 `zvms_new` 两个库。

此外后端启动依赖同目录下的密钥文件（上游生成脚本）：

- `generate_key.sh` → 生成 `rsa_public_key.pem` / `rsa_private_key.pem` / `aes_key.txt`。

> 以上文件均属敏感凭证，禁止提交进版本库。

### 5.2 运行后端

```bash
cd zvms4-backend-python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 生成密钥后
uvicorn main:app --host 0.0.0.0 --port 8000
```

`config/` 内含 `supervisor.conf` 与 `nginx` 样例，可用于生产托管（路径按实际部署调整）。

### 5.3 前端

前端保持上游 zvms4，按上游方式构建（`npm install` / `npm run build`），产物由 Nginx 或后端静态目录托管。

## 6. 已知事项 / 待办

- ~~**zvp 代码待审核**：本次只吸收了 zvp 的数据结构，未采用其任何业务代码。~~ → **义工管理面已审计并移植（2026-10-09）**：v3 四端点 + 权限修复，逐文件扫描确认无外链 / 外呼 / 越权代码，范围外危险代码（学海代理 `xh_router` / `api/xh.js`、平台登录 `platform.py`、ManageHome 外站跳转、外部图标 URL）均未引入，审计记录见 `STATUS.md`。其余 zvp 模块（安全中间件、其他 v3 路由等）若要参考仍需单独安全审计。
- **`categories` / `debug_descriptions`**：`conversion/groups.py`、`tasks/categorize.py` 会用到，但现役库中不存在、zvp 也无对应代码。属 zvms4 独有的一次性/调试集合，首次写入 MongoDB 会自动创建；如需正式支持需另行建表。
- **`groups_v2_router.py` 成员读取、`times_router.py`**：仍用遗留 `zvms.users`，与现役 zvp 行为一致，故保留。
- **索引**：现役两库除 `_id_` 外无任何自定义索引；本 fork 未新增索引，性能问题按需另行评估。
