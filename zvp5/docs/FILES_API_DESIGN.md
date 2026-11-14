# Files API 设计（照片上传 / 义工单取证）

> **v2（2026-10-14）**：已吸收 oracle 安全/架构审计（3×P0、5×P1、若干 P2/P3），并锁定全部决策。状态：**可实施**。
> 基线仓库：`zvp5/`（GitHub `xiaobo2010/zvp5`），HEAD `0ada10b`。
> 来源：三只读勘察泳道（Oracle 持久化约定 / 上传·鉴权·前端现状 / 照片上传最佳实践）+ 一轮 oracle 安全审计。

> **路径策略变更（2026-10-19）**：本文档只覆盖附件上传（Oracle BLOB），不涉及系统文件管理。另行修订：`/api/system/files` 的**系统目录黑名单已放开**（`/etc`、`/root`、`/usr`、`/dev`、`/var/lib/*` 等由 OS 权限裁决），仅保留 `/proc`、`/sys` 两个虚拟文件系统保护。详见 `V4242_STAGE2_CONTRACT.md` §3.3。
>
> **2026-10-29 取代上一条**：黑名单方案改为**根目录白名单 jail**——系统文件管理只允许 `/www/zvms4`，越界 403 `FILES_ROOT_FORBIDDEN`。见 §13；附件 API 本身不受影响。（2026-11-14 修订：`/home` 分支因生产 systemd `ProtectHome=true` 从未生效，正式从白名单删除。）

---

## 0. TL;DR（已锁定）

1. **存储**：二进制入 **Oracle BLOB**（延续 `imports.content` 先例），元数据同表；服务端**归一化重编码**压体积。不引入 MinIO/S3、不走本地 FS 静态托管。
2. **命名空间**：`/api/attachments`。**不用 `/api/files`**（`main.py:88` `SEC_PATTERN` 含 `r"api/file"` 子串，会 418）。不改 `SEC_PATTERN`。
3. **引用**：内部＝repo 直取（不过 HTTP）；外链＝**无状态 HMAC 签名 URL**（绑 `oid|exp|purpose`，密钥按用途派生）。**不加 `file_links` 吊销表**（靠 serve 端 status gate + 短 exp + 签发审计覆盖）。
4. **OCR**：本次只落**可插拔接口 + 状态机**，不接具体引擎。
5. **安全基线（v2 强化）**：解压炸弹三件套；`serve` 端 status gate（软删/隔离即时断供）；Pillow 全链走 threadpool + 并发信号量；sha256 仅同 owner 去重；日志不记 query。
6. **部署假设**：**单 uvicorn 进程**（`config/supervisor.conf`，无 `--workers`）——并发与内存预算按此设计。

---

## 1. 现状约束（已核实，附出处）

| # | 事实 | 出处 |
|---|---|---|
| C1 | 数据层迁 **Oracle ADB**，经 `db/` repo 协议层访问；行形状仍是 Mongo dict（`_id` = 24-hex 小写） | `db/backends.py:1-9`、`db/repos/protocols.py:8-18` |
| C2 | 二进制入库**已有先例**：`IMPORTS.CONTENT` 为 `BLOB`，repo 直接 bind `bytes`（**只有写、无读，且从未连库运行验证**） | `V001:494-510`、`db/repos/oracle/imports.py:78-96` |
| C3 | 新表须 `oid VARCHAR2(24 CHAR)` + `ck_*_oid` 正则、naive `TIMESTAMP` 由代码写、幂等 additive 迁移 | `db/oracle/ddl/README.md`、`db/models.py:40,488-500` |
| C4 | **`db/ddl_check.py:50` 只解析 `V001`**；既定约定是新对象 **V001（fresh）+ V00N（存量）双写** | `db/ddl_check.py:50`、`ddl/README.md:26-32` |
| C5 | 现有 `imports` 上传：`UploadFile`，**全量 `await payload.read()` 进内存**，仅 content-type 白名单，**无大小上限**，`except Exception → 400 str(e)`（泄漏内部信息） | `imports_router.py:17-45,58,141-142` |
| C6 | `pillow==12.0.0` 已装但**零图片/OCR 代码**；无 tesseract/opencv/pytesseract | `requirements.txt:34` |
| C7 | 鉴权：JWT `per` ∈ `student/secretary/department/admin`；`compulsory_temporary_token`→`scope=temporary_token`（5min）；**`get_current_user` 不查库**（banned 只在登录点拦，token 最长 10 天仍有效） | `util/cert.py:127-149,180-181`、`util/object_id.py:74-102` |
| C8 | `SEC_PATTERN` 含 `r"api/file"`；中间件要求 UA 含 `Mozilla`，否则 418 + 写 `security_alert`（URL 全量含 query） | `main.py:78-122` |
| C9 | 应用代码**零出站 HTTP**（`requirements.txt` 有 `httpx==0.28.1` 但仅供 TestClient，代码零命中）——既有红线 | 全树 grep；`STATUS.md` Part 4 §23 |
| C10 | 树内代理配置是 **nginx 文件**（`config/nginx`，`access_log` 记全 URI）；宿主实际跑 Caddy，配置在仓库外 | `config/nginx` |
| C11 | **单 uvicorn 进程**（无 `--workers`）；DB 池 `pool_size=5 + max_overflow=5` | `config/supervisor.conf`、`db/oracle/engine.py` |
| C12 | 前端 axios baseURL `/api` + 注入 Bearer；上传先例仅 Element Plus `<ElUpload>`，无 axios FormData | `src/plugins/axios.ts:11-22,57-64`、`ZUploadFile.vue:73-93` |
| C13 | `util/response.py::generate_response` **缺 `return`**（返回 `None`）；端点均内联响应体 | `util/response.py:9-17` |
| C14 | `inject_log` 记 `str(request.url)`（**含全量 query**）；`database.py`/`clob.py` 只处理 `str>4000→CLOB`，**无 bytes→BLOB 大对象 bind 分支** | `typings/log.py:74`、`db/oracle/clob.py` |
| C15 | Starlette 解析层 `spool_max_size=1MB`：**文件 part 超 1MB 即 spool 落 /tmp**，且 `File(...)` 在 handler 前已完成全量 parse；`max_files` 默认 1000 | `starlette/formparsers.py:126-136` |

> **C9 是决定性约束**：MinIO/S3 会引入出站 HTTP，与既有「零外呼」红线冲突 → 排除。

---

## 2. 决策矩阵

### 2.1 二进制存哪（**已定：Oracle BLOB**）

| 维度 | **Oracle BLOB ✅** | 本地 FS + Caddy | MinIO/S3 | Mongo GridFS |
|---|---|---|---|---|
| 与 C1/C2 一致 | ✅ 同库同备份 | ✗ 需手工对账 | △ | ✗ 已退役 |
| 与 C9 零外呼 | ✅ | ✅ | ❌ 出站 | ✗ |
| 事务/备份 | ✅ | ❌ | △ | 同 BLOB |
| DB 膨胀 | △（重编码+缩略图+软删清理对冲） | ✅ | ✅ | △ |
| 生命周期 | △ 自建清理任务 | △ cron | ✅ | △ |
| 运维复杂度 | ✅ 零新增 | △ | ❌ | — |

依据：OWASP 允许「有 DBA 且确属改进」时入库并警告其性能/备份代价；学术结论 <256KB 库内更优、>1MB 文件系统更优（arXiv cs/0701168）。本项目照片重编码后通常 <1.5MB，叠加 C9 红线 + C2 先例 → BLOB 是唯一不过度设计的路线。

### 2.2 其余分叉（**均已锁定**）

| 分叉 | 决定 | 未采用 |
|---|---|---|
| 公共前缀 | ✅ `/api/attachments` | `/api/media`；改 `SEC_PATTERN` |
| 上传方式 | ✅ 服务端 multipart + 归一化 | presigned 直传 |
| 外链 | ✅ HMAC 签名 URL | 仅 Bearer；`file_links` 吊销表（**不需要**，见 §6.2） |
| OCR 引擎 | ✅ 只留接口，不实现引擎 | Tesseract / PaddleOCR / Ollama 视觉 |
| UA 门 | ✅ 维持现状（外链仅限浏览器） | 豁免 attachments GET |

---

## 3. 数据模型

### 3.1 迁移（**V001 与 V007 双写**，C4）

`files` / `file_ocr` / `file_events` 三表**同时**进 `V001`（fresh 实例）与 `V007__add_files.sql`（存量实例），并镜像进 `db/models.py`，否则 `python -m db.ddl_check` 立刻 `FAIL files: missing from V001`。

沿用约定：`EXECUTE IMMEDIATE` + 吞 `-955/-1408/-1430/-2264/-2443`；`oid` 主键 + `ck_*_oid`；naive `TIMESTAMP` 代码写入；无序列/trigger。

```sql
CREATE TABLE files (
  oid           VARCHAR2(24 CHAR) NOT NULL,
  owner_oid     VARCHAR2(24 CHAR),              -- 上传者；不加 FK（与 V006 放宽 user FK 一致）
  kind          VARCHAR2(16 CHAR) NOT NULL,     -- original | thumb | derived
  parent_oid    VARCHAR2(24 CHAR),              -- thumb 指向原图
  mime          VARCHAR2(64 CHAR) NOT NULL,     -- 规范化、由 magic 实测得出
  size_bytes    NUMBER(19) NOT NULL,
  width         NUMBER(10),
  height        NUMBER(10),
  sha256        VARCHAR2(64 CHAR) NOT NULL,     -- 归一化后哈希（仅同 owner 去重）
  content       BLOB,                           -- 归一化字节；tombstone 时置 NULL（见 §3.3）
  original_name VARCHAR2(255 CHAR),             -- 仅展示，入库前截断+去控制字符
  purpose       VARCHAR2(32 CHAR),              -- v1 白名单：volunteer_order
  entity_type   VARCHAR2(32 CHAR),
  entity_oid    VARCHAR2(24 CHAR),
  status        VARCHAR2(16 CHAR) NOT NULL,     -- 见下方 CHECK（含 verified/rejected）
  ocr_status    VARCHAR2(16 CHAR) NOT NULL,
  ocr_retry     NUMBER(3) DEFAULT 0 NOT NULL,
  created_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP,
  deleted_at    TIMESTAMP,
  PRIMARY KEY (oid),
  CONSTRAINT ck_files_oid     CHECK (REGEXP_LIKE(oid,'^[0-9a-f]{24}$')),
  CONSTRAINT ck_files_kind    CHECK (kind IN ('original','thumb','derived')),
  CONSTRAINT ck_files_status  CHECK (status IN
      ('stored','processing','ready','verified','rejected','quarantined','deleted')),
  CONSTRAINT ck_files_ocr_status CHECK (ocr_status IN ('pending','processing','done','failed'))
);
-- 索引（审计补全）：
--   ix_files_owner(owner_oid, created_at)
--   ix_files_entity(entity_type, entity_oid)
--   ix_files_sha256(owner_oid, sha256)      -- 同 owner 去重查找
--   ix_files_parent(parent_oid)             -- /thumb 反查
--   ix_files_cleanup(status, deleted_at)    -- 物理清理
--   ix_files_ocr(ocr_status)                -- OCR 轮询
```

**P0-1 修正**：`status` 是**唯一事实源**，CHECK 含 `verified/rejected`。状态机：`stored → processing → ready → verified|rejected`，异常分支 `quarantined`；软删 `deleted`。`DELETE` 门 = `status NOT IN ('verified','rejected')`（由 `status` 直接判定，不扫事件流）。`file_events` 是**审计副本**，不参与判定。

**SECUREFILE**：`LOB (content) STORE AS SECUREFILE (...)` 可写；实测 `ddl_check.py:186-187` 只解析 `CREATE TABLE` 括号内 body，**尾部存储子句不进解析器**（不会红，也无保障）——可安全添加。

**`owner_oid` 不加 FK** ✅（与 `V006` 放宽 user FK 一致）。

### 3.2 repo 协议 + 实现（**冻结签名**，供后端两条泳道对齐）

`db/repos/protocols.py` 增 `FilesRepo(Protocol)`；`db/repos/oracle/files.py` 实现 `OracleFilesRepo`（`_session_for` + `session_scope()`、`new_oid()`）。**读/写 BLOB 是新能力**（仓内无先例，只有 `imports` 的写）：需处理 bytes↔BLOB，并预验大对象 bind（见 P2-4）。

```python
class FilesRepo(Protocol):
    async def create(self, owner_oid, kind, parent_oid, mime, size_bytes, width,
                     height, sha256, content: bytes, original_name, purpose) -> str: ...
    async def get_meta(self, oid: str) -> Row | None      # 绝不 select content
    async def get_content(self, oid: str) -> bytes | None # 仅字节
    async def find_by_sha256(self, owner_oid: str, sha256: str, kind: str) -> Row | None  # 同 owner 去重
    async def list_page(self, owner_oid: str | None, purpose: str | None, status: str | None,
                        entity_type: str | None, entity_oid: str | None,
                        page: int, size: int) -> tuple[list[Row], int]   # 恒不 select content
    async def thumb_for(self, parent_oid: str) -> Row | None
    async def set_status(self, oid: str, status: str) -> None
    async def set_entity(self, oid: str, entity_type: str, entity_oid: str) -> None
    async def set_ocr(self, oid: str, ocr_status: str, *, bump_retry: bool = False) -> None
    async def soft_delete(self, oid: str) -> None
    async def purge_deleted(self, before: Any) -> int      # 物理清理（含 content=NULL）
    async def add_ocr(self, file_oid, engine, engine_version, text, confidence, raw) -> str
    async def get_ocr(self, file_oid: str) -> Row | None
    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str
    async def list_events(self, file_oid: str) -> list[Row]
```

**关键**：`get_meta`/`list_page` **必须投影列**，SELECT 含 `content` 会把整 BLOB 物化进内存（oracledb 默认 fetch LOB）。

`db/backends.py` 增 `get_files_repo()` 并入 `__all__`。

### 3.3 软删 / 留存 / tombstone 语义（P0-3 + P2-6）

- 软删 = `status='deleted'` + `deleted_at`；**`serve` 端即时断供**（见 §4）。
- 物理清理（APScheduler 每日任务，宿主同 `main.py:145` 的 scheduler）：`status='deleted' AND deleted_at < now-retention` → `content=NULL` 保留元数据 tombstone；更久后删行。
- 留存窗：`rejected/quarantined` 30 天后清字节；`verified` 保留字节直至义工单归档（可配）。

---

## 4. API 面（`/api/attachments`）

约定：`Depends(get_current_user)` + `user["per"]` 检查 + `Depends(inject_log)`；错误 `HTTPException(400/403/404/410/413/415)`；响应体内联 `{"status","code","data"}`（**绕开 C13 的 `generate_response`**）。

| # | 方法 & 路径 | 逐行权限（P2-1） | 说明 |
|---|---|---|---|
| 1 | `POST /api/attachments` | 登录（**banned 复查**，见 P2-7）；学生**不接受 `entity_*`**（P2-2），传则 400 | multipart 文件（`max_files=5`）；`purpose` 白名单校验；校验→归一化→入库→返回元数据 |
| 2 | `GET /api/attachments/{oid}` | **owner 或 admin/department/secretary 同班** **或** 有效签名 | 字节。**serve gate：`status IN ('stored','ready','verified')`**，否则 410；`deleted/quarantined/rejected` **签名有效也拒** |
| 3 | `GET /api/attachments/{oid}/meta` | 同 #2 读权限（**非 owner 不回 `sha256`**，P1-5） | 仅元数据（**永不 `content`**） |
| 4 | `GET /api/attachments` | `scope=all` **服务端**判 `admin|department ∈ per`，否则 403 | 过滤 `purpose/status/entity_*`；分页（`page=-1` 不分页） |
| 5 | `GET /api/attachments/{oid}/thumb` | 同 #2 | 缩略图字节（同 gate） |
| 6 | `POST /api/attachments/{oid}/link` | owner / admin / department | 签发外链 `{url, expires_at}` |
| 7 | `POST /api/attachments/{oid}/ocr` | admin / department（幂等） | 入队（重跑）**走网关同一套引擎池**：频控 → 用户额度 `ocr` → 按 `priority` 选站点额度还够的引擎；`processing` 时返回当前状态且不重复计价 |
| 8 | `GET /api/attachments/{oid}/ocr` | owner / admin / department | `{status, engine, engine_version, text, confidence, updated_at}` |
| 9 | `POST /api/attachments/{oid}/verify` | admin / department | `{decision: verified\|rejected, note}` → `set_status` + `file_events` |
| 10 | `POST /api/attachments/{oid}/bind` | admin / department | `{entity_type, entity_oid}` |
| 11 | `DELETE /api/attachments/{oid}` | owner（`status NOT IN ('verified','rejected')`）或 admin | **软删** |

**读权限矩阵**：owner（self）✅ / admin ✅全 / department ✅全 / **secretary 同班 ✅（对齐 `util/permission/user.py:32-40` 既有语义）** / 其它登录用户 ❌。写门 = admin/department（`util/permission/volunteer*.py` 同族）。

**穿 C8 中间件**：字节端点走 `/api` 前缀（受 `SEC_PATTERN`/UA 门约束）——`/api/attachments` 不命中任何禁词（也不含 `doc`）✅；非浏览器抓取会被 418（P3-2，预期噪音）。

---

## 5. 上传与校验流水线（服务端）

对 C5 的根因修正。**P1-1**：Pillow 全链是 CPU-bound，必须 `anyio.to_thread.run_sync()` 执行，配全局 `asyncio.Semaphore(2)`；单进程部署（C11）下这是硬要求。

```
0. 前置：读 Content-Length，> MAX_BYTES(15MB) → 立即 413（不等 parse，P1-2b）
1. 解析：await request.form(max_files=5, max_fields=10)（P1-2c），替代裸 File(...)
   —— 注意 Starlette 已 spool 落 /tmp（C15），"不落盘"声明已更正
2. 分块读（await file.read(1MB) 循环）累计 size，超限 → 413
3. threadpool 内：Image.open() 取 im.format 判 jpeg/png/webp（不信 content-type）
4. 解压炸弹三件套（P0-2）：
   a. Image.MAX_IMAGE_PIXELS = 64_000_000          # 真硬上限（义工单照片足够）
   b. warnings.simplefilter("error", Image.DecompressionBombWarning)  # 警告升异常
   c. 显式限单边 width/height（如 ≤ 10000）
5. ImageOps.exif_transpose() 烘焙方向并丢弃 EXIF
6. 重编码：最长边 ≤2560 → convert("RGB") → JPEG q85 optimize（**只存归一化字节**）
7. sha256(归一化字节)；缩略图（最长边 320）作为 kind=thumb 独立行
8. 入库媒体行 + file_events；置 ocr_status=pending
```

- **大小上限双保险**：应用层 `MAX_BYTES` + 代理层 `request_body { max_size 20MB }`（Caddy；nginx 则 `client_max_body_size`）。Caddy 超大请求历史上有返 502 而非 413（caddy#5652），上线需实测。
- **/tmp 依赖**：spool 落盘（C15）+ 代理侧 cap 才是真正早停闸；`/tmp` 容量纳入运维检查（tmpfs 场景直接吃内存）。
- **错误分类**：415（类型）/413（过大）/422（损坏），`inject_log` 记**业务日志**（不复刻 C5 的 `str(e)` 泄漏）。
- **配额**（P2-3）：每用户每日张数/字节配额 + 全局上传信号量（DB 池仅 10，ADB 高延迟下 15MB INSERT 持连接久）。

---

## 6. 引用模型

### 6.1 内部引用
同进程 `backends.get_files_repo().get_meta/get_content(oid)`，**不过 HTTP、不过中间件、不产生外链**，供 OCR/查证/报表使用。业务表只存 `oid`。

### 6.2 外链引用（HMAC 签名 URL）

- 密钥按用途派生（与 Part 4 TOTP 同法）：`key = HMAC-SHA256(aes_key_bytes, "filesign")`，与 JWT HS256 裸钥隔离。
- 报文：`sig = base64url(HMAC-SHA256(key, f"{oid}|{exp}|{purpose}"))`；URL `?exp&purpose&sig`。
- **校验顺序（P1-3）**：先 `hmac.compare_digest` 验签 → 再验 `exp`（服务端时钟）→ 再查库；**无效签统一 403，无论 oid 是否存在**（消除存在性 oracle）。
- **签名只覆盖已校验值**：`exp`/`purpose` 参与签名，篡改即验签失败。
- **有效期**：默认 `≤1h`（可配，上限 24h）；`POST .../link` 才签发并写审计。
- **即时吊销**：不设吊销表；软删/隔离由 §4 serve gate 直接断供。
- **响应头**：`Referrer-Policy: no-referrer`；敏感场景 `Cache-Control: private, no-store`（P3-3）。

### 6.3 与既有一次性 token 的差异
`exports` 下载 token 是**内存 list**（多 worker 不共享、重启即失）——files **不沿用**，改无状态 HMAC（需要状态时才考虑 `file_links`，本次不需要）。

---

## 7. OCR 与查证流水线

- **状态机**：`stored → (ocr pending→processing→done|failed) → verified|rejected`；每次跃迁写 `file_events`。
- **异步范式**：仿 `export_tasks` 的 **DB 状态列模式**，但**不仿其阻塞**（`exports_router.py:117` 在 async 里 `time.sleep` 阻塞 loop，P1-1）——执行一律 `to_thread`。
- **引擎可插拔（本次只落接口）**：`OcrEngine`（`name/version/recognize(bytes) -> {text, confidence, raw}`）+ no-op 占位；把 `POST .../ocr` 与状态机跑通，后续接 Tesseract/PaddleOCR **API 面不变**。结果写 `file_ocr`（`engine/engine_version/raw_json`）；OCR 读归一化字节保证可复现，可按 `sha256+engine_version` 跳过。
- **查证**：`verify` 记录人工判定 + 备注；`file_ocr.text` 供比对；审计链完整。

---

## 8. 安全设计

### 8.1 上传面
分块读 + 双限大小 + magic-bytes + **炸弹三件套** + EXIF 剥离 + 重编码；`original_name` 截断 255 + 去控制字符（P3-1）；`purpose` 白名单 `volunteer_order`（P3-1）。

### 8.2 响应头
`X-Content-Type-Options: nosniff`、规范化 `Content-Type`、`Content-Disposition: inline; filename="<oid>.<ext>"`、`Cache-Control: private`（敏感 `no-store`）、`Referrer-Policy: no-referrer`、`Content-Security-Policy: default-src 'none'`。

### 8.3 `SEC_PATTERN`（C8）
`r"api/file"` 会 418 掉 `/api/files` → 用 `/api/attachments`，**不动安全控制**。避免路径含 `doc`（`r"doc"` 亦子串匹配）。

### 8.4 UA 门（C8）
`Mozilla` 要求下，浏览器 `<img>` 外链正常；非浏览器被抓 418 + 写 `security_alert`（预期噪音，运维知晓）。OCR/查证在进程内，不受影响。**已定：维持现状。**

### 8.5 审计与日志（P1-3 + P3-5）
- 上传/签发/OCR/查证/绑定/删除全写 `file_events`。
- **字节端点日志只记 `request.url.path` + oid，绝不记 query**（`sig` 是可复用凭证；`inject_log` 当前记全 URL，C14）。
- uvicorn 侧确认不记 query（`--no-access-log` 或核对部署形态）；Caddy/nginx access log 同核对（C10）。
- `file_events` repo 只暴露 `insert` + `list`（GET-only），与 Part 3「不能随意修改日志」一致。

### 8.6 sha256 去重（P1-5）
**仅同 owner 内**用于秒传跳过与完整性校验；**跨 owner 永不去重、永不共享行**；`meta` 对非 owner 不回 `sha256`。

### 8.7 已知平台级缺口（记录，不本次修）
banned 用户 token 最长 10 天仍有效（C7）——files 上线会被穿透；本设计在 #1 上传端点**加 DB 层 banned 复查**兜底（P2-7）。

---

## 9. 部署与反向代理

- 代理层上传体上限 + 超时：Caddy（宿主）`request_body { max_size 20MB }` + `servers { timeouts { read_body ... } }`；nginx（树内）`client_max_body_size` + `client_body_timeout`。**树内配置与实际部署可能不一致（C10），须在部署机核对。**
- 清理任务挂 APScheduler（`main.py:145` 既有 scheduler），每日跑 `purge_deleted`。
- 无需静态挂载、无需改 CORS；无出站、无新服务。

---

## 10. 前端接线（最小改动）

- 新增 `types/attachment.d.ts` + `src/api/attachment/*.ts`（沿用目录惯例与 `result.data.data` 解包）。
- 拍照上传：`<input type="file" accept="image/*" capture="environment">`，或复用 Element Plus `<ElUpload>` 直发 `POST /api/attachments`（带 `Authorization`）。
- 展示：`GET /api/attachments/{oid}`（`<img src>`）或签名 `url`。
- **不重构既有页面**；只交付可即插的 API 层 + 上传/展示组件。

---

## 11. 已拍板的分叉（2026-10-14）

| 分叉 | 决定 |
|---|---|
| 存储 | ✅ Oracle BLOB（不 FS / MinIO） |
| 前缀 | ✅ `/api/attachments`（不动 `SEC_PATTERN`） |
| 外链 | ✅ 无状态 HMAC 签名 URL（不加 `file_links`） |
| OCR | ✅ 只留接口 + 状态机，不接引擎 |
| UA 门 | ✅ 维持现状 |

oracle 审计追加确认：`file_links` 吊销表**不需要**；`owner_oid` **不加 FK**；SECUREFILE 子句可写但 `ddl_check` 不校验。

---

## 12. 验证计划（实现后）

- **静态**：`python -m db.ddl_check` 绿（三表须同时在 V001+models.py）；`python3 -m compileall` exit 0；`vue-tsc --noEmit` 改动文件 0 error。逐条贴原始输出。
- **运行时冒烟**（需 ADB 环境）：
  - 上传各类型（jpeg/png/webp/伪造 content-type/超大/40k×6k 炸弹）→ 归一化元数据正确、炸弹被拒；
  - **BLOB 读回**（仓内首次，P2-4 必验）→ 字节与 sha256 一致；
  - 外链签名：有效 / 过期 / 篡改 / **跨 oid 重定向** 均符合预期；
  - **软删后 gate**：Bearer 与未过期签名 URL 均 410；
  - 越权读/删/绑 403；`scope=all` 客户端伪造 403；
  - OCR 入队→done（占位引擎）；verify 审计链完整。
- **安全复核**：nosniff/Content-Type/Disposition/Referrer-Policy 头齐全；无路径可穿越；签名不可重定向；日志无 query、无异常信息泄漏。

---

## 附：顺带发现的 smell（不在本任务范围）

`util/response.py:9-17` `generate_response` **缺 `return`**（返回 `None`，C13）；`exports_router.py:42` 内存 token 非持久（多 worker 隐患）。建议单独立项。

---

## 参考来源

- OWASP File Upload Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- arXiv cs/0701168（DB vs FS 大对象）— https://arxiv.org/pdf/cs/0701168
- FastAPI Request Files — https://fastapi.tiangolo.com/tutorial/request-files/
- SQLAlchemy Oracle LOB — https://docs.sqlalchemy.org/en/20/dialects/oracle.html
- Caddy `request_body` — https://caddyserver.com/docs/caddyfile/directives/request_body ；413/502 — https://github.com/caddyserver/caddy/issues/5652
- Pillow `MAX_IMAGE_PIXELS` / `verify()` / `exif_transpose` — https://github.com/python-pillow/pillow/blob/main/src/PIL/Image.py
- MDN `X-Content-Type-Options` — https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Content-Type-Options

---

## 13. 系统文件管理的根目录 jail（2026-10-29）

系统文件管理（`/api/system/files`）与 ZVAgent 的 `fs_*` 工具共享同一个路径判定器 `services/file_ops.py`。本轮把「靠 OS 权限裁决」改成**显式白名单**：

```python
ALLOWED_ROOTS = ("/www/zvms4",)
ROOT_FORBIDDEN_CODE = "FILES_ROOT_FORBIDDEN"
```

判定顺序（不可调换）：

1. 输入必须是绝对路径；拒绝 NUL / 换行。
2. `os.path.realpath` 展开 `..` 与符号链接。
3. 真实路径必须等于某个根，或以 `root + os.sep` 开头——按**路径段**比较，`/www/zvms40` 不会因为以 `/www/zvms4` 开头被放行。
4. 再判 `/proc` / `/sys` 虚拟文件系统与凭据文件黑名单（`aes_key.txt`、`*.pem`、`settings.py`、`.env*`、wallet 等）。

效果：

- `..` 与符号链接都逃不出这两个根；`/etc`、`/root`、`/usr`、`/var/lib/*` 等以前由 OS 权限裁决的路径现在直接 403。
- 越界响应：`403`，`detail` 为错误码 `FILES_ROOT_FORBIDDEN`（人类可读句在服务层，路由层统一翻译）。
- 同一判定同时约束 HTTP 端点和 agent 工具：不存在「文件管理器进不去、agent 能读」的缝隙。
- 前端快捷目录与路径跳转同步收敛到这两个根，越界在提交前就被拦（后端仍是最终裁决）。

受影响的端点：`/api/system/files` 的 list / stat / preview / download / upload / mkdir / rename / delete / chmod / edit，以及 `/api/ai/ocr/file`（路径桥，见 `docs/AI_GATEWAY_API.md` §2.5）。
