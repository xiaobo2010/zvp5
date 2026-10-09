# ZVMS4 Backend — ZVP Modified v4

> 上游 **zvms4-backend-python**（`zvms/zvms4-backend-python`）的定制版本。
> 业务逻辑与路由以上游为基准；**数据库层已向现役 zvp 平台真实结构对齐**。
> 顶层说明与对齐总览见 `../README.md`，数据库真实结构与逐条改动见 `../DB_ALIGNMENT.md`。

## 技术栈

FastAPI + Motor(async MongoDB) + Pydantic；可选 socket.io / APScheduler（上游保留）。

## 数据库（关键）

后端连接 **两个 MongoDB 库**（`database.py` 固定）：

| 库 | 用途 | 主要集合 |
|---|---|---|
| `zvms` | 遗留 | `users`(id,name,sex,group[],password,past)、`groups`(name,description,permissions[],type)、`tasks`、`logs`、`imports`、`notifications` |
| `zvms_new` | 现役主库 | `users`(id,name,roles[],class_id,password,session_id)、`groups`(name)、`activities`、`activity_members`、`time`、`time_academic_year`、`time_with_origin`、`time_with_origin_stat`、`indicators`、`group_indicators`、`daily_exports`、`logs` |

集合归属规律（与现役 zvp 一致）：

- **v2 / v3、exports / imports、登录鉴权** → `zvms_new.*`
- **v1（用户列表、班级 v1）与 `util/*`** → 遗留 `zvms.*`

完整字段/类型与本次对齐的 11 处改动，见 `../DB_ALIGNMENT.md`。

## 配置

```bash
cp settings.example.py settings.py
```

- `MONGODB_URI`：数据所在的 MongoDB 连接串（当前现役为 Atlas 集群）。
- `MONGODB_DB`：按上游约定不需要；两库名在 `database.py` 内固定。
- 启动依赖 `generate_key.sh` 生成的 `rsa_public_key.pem` / `rsa_private_key.pem` / `aes_key.txt`（属敏感凭证，勿入库）。

## 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m compileall .            # 快速自检
uvicorn main:app --host 0.0.0.0 --port 8000
```

生产托管样例见 `config/supervisor.conf` 与 `config/nginx`。

## 本次改动范围

仅数据库层：`util/cert.py`、`routers/users_router.py`、`routers/users_v2_router.py`、`routers/exports_router.py`、`routers/imports_router.py`、`typings/group.py`。其余文件与上游 zvms4 一致。
