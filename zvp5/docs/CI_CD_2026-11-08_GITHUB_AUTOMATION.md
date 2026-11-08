# GitHub 自动化流水线移植与审查（2026-11-08）

## 一、来源与形态

- 源分支：`origin/codex/github-automation`（作者 `albxy`，4 个提交，
  `8e4a654` → `38a52bf`），基线 `c965429`。
- 形态是 **push 式发布**：GitHub Actions 跑质量与安全检查 → 构建并打包
  → SSH 把制品送到两台主机 → 服务器端 `/usr/local/sbin/zvms4-deploy`
  （仅这 4 条命令有免密 sudo）校验 sha256、备份、原子切换、健康检查，
  失败自动回滚。**不是服务器端 `git pull` 轮询。**
- 门禁语义：`push` 到 main 只跑 CI；只有发布**非预发布 Release**、且
  release commit 是 `origin/main` 祖先时才部署。DDL 只告警、不执行。

## 二、为什么不能直接 merge

main 已把 `zvms4-modified-main/` 更名 `zvp5/`，分支仍用旧目录名；
直接 merge 会重建旧目录树并回滚 main 的 V052 迁移、文档与测试
（两棵树相差 1067 个文件）。因此按「移植 + 路径适配」落地：从分支取
内容，改写到 `zvp5/` 路径，再与人工作出的修复一起进 main。

## 三、移植清单（33 文件）

新增：

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/ci.yml` | 根流水线（GitHub 只执行根 `.github/workflows`） |
| `.github/ISSUE_TEMPLATE/*`、`.github/PULL_REQUEST_TEMPLATE.md` | 模板 |
| `.github/dependabot.yml` | 每周分组更新 pip / pnpm / actions |
| `.gitleaks.toml`、`.gitleaks-high-confidence.toml` | 密钥扫描规则 |
| `zvp5/deployment/github-actions/zvms4-deploy` | 服务器端部署/回滚脚本 |
| `zvp5/deployment/github-actions/bootstrap-deploy-user.sh` | 建部署账号 + 收窄 sudoers |
| `zvp5/deployment/github-actions/test-deploy-wrapper.sh` | 部署脚本回归测试 |
| `zvp5/deployment/github-actions/README.md` | 运维手册 |
| `zvp5/zvp5-backend-python/tests/route_utils.py` | 展开 FastAPI `_IncludedRouter` 的测试辅助 |

修改：根 `README.md`（第十节）、`docs/CORS_PREFLIGHT_2026-11-07.md`（事故复核 +
自动化防回归）、`docs/DEPLOY_API_SPLIT.md`、后端依赖与 lifespan 迁移、前端依赖
与锁文件、`SECURITY.md`。

删除：`zvp5/.github/workflows/ci.yml`（嵌套目录里的旧 CI，GitHub 从不执行）、
`zvp5/zvp5-backend-python/renovate.json`（改用 Dependabot）。

## 四、审查发现与修复

| 级别 | 问题 | 处置 |
| --- | --- | --- |
| P0 | `release.tag_name` 直接插值进 `run:`（两处），tag 名可含 `$()`/反引号；该 step 执行时两把生产 SSH 私钥已落盘 → runner RCE | 改为经 `env: RELEASE_TAG` 传入，脚本内只做变量展开 |
| P0 | `workflow-security` 从仓库根引用 `deployment/github-actions/*`，但文件在 `zvms4-modified-main/deployment/` 下 → job 必挂、deploy 永不可达 | 脚本落到 `zvp5/deployment/github-actions/`，CI 路径同步；本地 shellcheck/actionlint 复验 |
| P1 | gitleaks 白名单仍是旧路径（`zvms4-modified-main/...`），测试夹具与 spec 不被豁免（实测原配置误报 29 处） | 改写为 `zvp5/...`；复扫 0 |
| P1 | root 解包入站 tar 未收敛权限位，可保留 setuid（制品与校验值走同一部署账号通道，校验和防篡改能力有限） | 全部 `tar -xzf` 加 `--no-same-permissions`；新增体积上限 256MB；补 setuid/超限/校验和不符三条回归用例 |
| P1 | README 承诺保留 `uploads`，但 exclude 清单没有它，`rsync --delete` 会静默删运行期目录 | 服务器端与 workflow 打包两处 exclude 同步补齐 `uploads`、`instance`、`logs`、`local_settings.py*`、`*.log` |
| P1 | 部署权只受 tag 内容约束，未约束谁能建 tag | 运维 README 增加 tag 保护 ruleset / required reviewers 两条硬要求 |
| P2 | sudoers 未固化 env 基线，宿主全局策略变化会让 `ZVMS4_DEPLOY_PATH`/`SYSTEMCTL` 变成 root 通道 | 生成文件内加 `env_reset` + `secure_path` |
| P2 | `~/incoming` 制品只增不删 | 部署成功后清理本次制品（失败不清理，便于排障） |
| P2 | SSH 未设快速失败 | 加 `BatchMode=yes`、`ConnectTimeout=15`、`GlobalKnownHostsFile=/dev/null` |

## 五、依赖与运行时升级（为何必须随行）

安全门禁不是装饰：不升级就永远红。

| 扫描 | main 现状 | 升级后 |
| --- | --- | --- |
| Trivy（后端 requirements） | 71 项（63 HIGH / 8 CRITICAL，含 PyJWT 认证绕过） | 0 |
| `pnpm audit --prod --audit-level high` | 55 项（28 HIGH / 1 CRITICAL，含 dompurify、axios、ckeditor、wrap 链） | 0 |
| FastAPI / Starlette | 0.124 / 0.50 | 0.162 / 1.10 |

- Starlette 1.10 移除了 `add_event_handler`，故必须迁移到 lifespan
  （`create_app(lifespan=...)` + `main.lifespan`），并按新版 `_IncludedRouter`
  调整路由断言（`tests/route_utils.py`）。
- `@ckeditor/*` 仅存在于 `src/main.ts` 的注释行，删除无功能影响；`@types/dompurify`
  无人引用；`vant` 已由 main 先行删除，本移植保留该删除（三方合并时人工确认）。
- 锁文件未照搬分支版本，而是以 main 的锁为基础、在合并后的
  `package.json` + `pnpm-workspace.yaml`（overrides）上重新生成，避免带回 `vant`。

## 六、验证凭证

- 后端（新依赖独立 venv）：`ruff --select F821`、`--select F401` 均 0；
  `pytest -q` **4496 passed**；`compileall` 通过。
- 前端：`pnpm install --frozen-lockfile`（pnpm 10.34.5）成功；
  `vitest run` **1054/1054**；`eslint` 0 error（仅既有 ZMarkdown `v-html`
  warning）；`vue-tsc --noEmit` 0；生产构建成功。
- 工作流与脚本：`shellcheck -x` 0；`actionlint 1.7.7` 0；
  `test-deploy-wrapper.sh` 通过（含新增的校验和拒收、体积超限、setuid 剥离）；
  `gitleaks v8.28.0`（干净检出）0；Trivy 0；`pnpm audit` 0。

## 七、首次启用前的人工步骤（本次未执行）

1. 两台目标主机各自跑一次
   `sudo bash zvp5/deployment/github-actions/bootstrap-deploy-user.sh ./zvp5-deploy /root/zvp5-deploy.pub`。
2. GitHub 建 `production` Environment，按 README 配 5 个 Variables / 10 个 Secrets。
3. 加 tag 保护 ruleset（限制 `refs/tags/*` 的创建与删除）。
4. 首次 release 前在目标机 `ls -la /www/zvms4/backend` 对账一次，确认是否存在
   `uploads` 等运行期目录（已加入 exclude，但需确认实际形态）。
5. 本次**没有部署生产**，也没有创建 release。

## 八、遗留（不影响本次合并）

- 服务器端无并发互斥（CI 侧有 `production-deploy` 串行组）；手工执行与 CI
  重试并发时存在竞态，可加 `flock`。
- 前端切换为两次 `mv`，存在毫秒级 404 窗口；可改为 releases 目录 +
  symlink 原子翻转。
- 第三方 action / 镜像仍为 tag 固定，未做 digest pin。
- 部署期 `pip install` 无 `--require-hashes`。
- DDL 变更基线用 `git describe --tags`，非 release tag 会干扰告警范围。
- 远端 rollback 在"尚未产生备份"时会报一条误导性失败，可让 wrapper 支持
  no-change 幂等。

## 九、启用后的自动化观察（2026-11-08）

合入 `0c0db42` 后，`dependabot.yml` 立即开始工作，远端生成 6 条待评审分支：

| 分支 | 内容 | 处置建议 |
| --- | --- | --- |
| `dependabot/github_actions/github-actions-*` | `checkout` v4→v7、`setup-python` v5→v7、`pnpm/action-setup` v4→v6、`setup-node` v4→v7、`upload-artifact` v4→v7 | 先看该 PR 自身的 CI 结论再合；6 个主版本一次性跳，需确认 action 的输入/输出语义没变 |
| `.../backend-patches-*` | 后端 patch 组（certifi、click、uvicorn 等） | 低风险，跑一遍后端全量即可 |
| `.../pycryptodome-4.3.0` | 3.23 → 4.x 主版本 | **暂不合**：加密/解密链路的 C 扩展大版本，需单独验证历史密文可解 |
| `.../sqlalchemy-eq-2.1.star` | 2.0 → 2.1 | **暂不合**：ORM 行为变更需跑全量 + 仓储层重点用例 |
| `.../redis-gte-8.1.0-and-lt-9` | 6.4 → 8.1 | **暂不合**：跳两个大版本，读穿缓存路径要实测 |
| `.../tencentcloud-sdk-python-ocr-*` | SDK 下限抬到 3.1.202 | 低风险，OCR 相关用例覆盖后即可合 |

原则：`main` 只在全量门禁绿的前提下前进；Dependabot PR 不批量合。
