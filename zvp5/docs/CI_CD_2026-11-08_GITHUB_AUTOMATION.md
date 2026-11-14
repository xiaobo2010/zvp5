# GitHub 自动化流水线移植与审查（2026-11-08）

## 一、来源与形态

- 源分支：`origin/codex/github-automation`（作者 `albxy`，4 个提交，
  `8e4a654` → `38a52bf`），基线 `c965429`。
- 形态是 **push 式发布**：GitHub Actions 跑质量与安全检查 → 构建并打包
  → SSH 把制品送到两台主机 → 服务器端 `/usr/local/sbin/zvp5-deploy`
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
| `zvp5/deployment/github-actions/zvp5-deploy` | 服务器端部署/回滚脚本 |
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

- ~~服务器端无并发互斥（CI 侧有 `production-deploy` 串行组）；手工执行与 CI
  重试并发时存在竞态，可加 `flock`。~~ **已修**，见第十节。
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

## 十、2026-11-14 审查收口

对 `origin/main`（`9343a48`，含 dependabot 的 6 个 actions 大版本跳）做了一次
CI/CD 专项审查，本轮修掉的问题：

| 级别 | 问题 | 处置 |
| --- | --- | --- |
| P0 | deploy/回滚路径用 `"${VITE_API_BASE%/}/health"` 拼健康检查 URL。标准值 `PROD_API_BASE=/api` 时它只是相对路径，curl 直接 `URL rejected: No host part`，**后端已部署成功也会判失败并整链回滚** | preflight 解析出的绝对地址写入 `$GITHUB_ENV`（`API_HEALTH_URL` / `RACE_API_HEALTH_URL`），deploy 与回滚路径只读这两个变量；写回前用 `^https://[^[:space:]]+$` 校验，避免多行值污染 `GITHUB_ENV` |
| P1 | `VITE_STATIC_BASE` 内联 fallback `https://zvpapi.xiaobopisces.top/`，与「生产域名不进 workflow」的约定冲突，且 `PROD_STATIC_BASE` 漏配会被静默兜底 | 删掉 fallback，改为必填变量 |
| P1 | 依赖/扫描镜像的新 CVE 只有 push/PR/release 才会被扫到 | 加 `schedule: 0 3 * * 1`（每周一）；`deploy-production` 的 `if` 限定 release 事件，定时运行不会部署 |
| P2 | 五个质量 job 的 checkout 把只读 token 留在 runner 的 git config 里 | 加 `persist-credentials: false`；`deploy-production` 保留（它要 `git fetch origin main`） |
| P2 | `dependabot.yml` 的 github-actions 组不限 `update-types`，主版本和补丁混在一个 PR（已发生 v4→v7/v8 六个一次性合并） | 组内限 `minor`/`patch`，主版本改为单独 PR |
| P2 | 服务端无并发互斥（第九节遗留） | wrapper 入口 `exec 9>…/deploy.lock` + `flock -w`，超时默认 600s（`ZVMS4_DEPLOY_LOCK_TIMEOUT`）；`test-deploy-wrapper.sh` 补「锁被占用时拒绝、释放后正常部署」用例 |

本轮明确不做：覆盖率/后端类型检查门槛（当前基线撑不住，会变成常驻红）、
releases 目录 + symlink 原子翻转（要动 nginx root）、`pip install --require-hashes`
（需先产出带哈希的锁文件）、action digest pin（等 canary 通道定稿后统一做）。

canary 通道单独处理：`v5-canary` 的 push 目前不触发任何 CI，前端预览站也仍是
手工 `wrangler` 发布。计划是复制一份 CI 配置到 canary，并把前端自动发到
Cloudflare Worker，本次未动 `on.push.branches`。

## 十一、canary 通道（2026-11-14）

`v5-canary` 之前是「推了不跑任何 CI、预览站全靠手工 wrangler」的状态。现在单开
一份 `.github/workflows/canary.yml`，与 main 的 `ci.yml` 完全解耦：

- 触发：`push` 到 `v5-canary`（仅当前端、preview-worker 或本文件变更）、或手动 dispatch。
- 门禁：**只跑前端**——vitest、lint、`pnpm run build`（内含 vue-tsc）与构建断言，
  和部署合并成**一个 job**，只构建一次。后端 pytest、Cypress、gitleaks、trivy 只在
  main 的 `ci.yml` 跑：canary 只产静态预览，后端回归在合入 main 时拦。
- 计费：私有库按 job 时长算，本通道约 **7~9 分钟/次推送**，main 全量一轮约 20 分钟；
  纯文档或纯后端提交不触发。
- 部署：只有 `deploy-canary` 一个 job，**只发前端静态产物**到 Cloudflare Worker
  `zvp-yuren`（`canary.xiaobopisces.top`）。无 SSH、无后端发布、无 DDL。
- 构建参数：`VITE_API_BASE`/`RACE`/`CONTENT` 指向正式 API 域（跨域 + 后端 CORS
  白名单），`VITE_STATIC_BASE=/`（Worker 自托管静态资源），
  `VITE_PAGE_BASE=https://canary.xiaobopisces.top/`（PWA scope 锚预览域）。
- 防回退：构建后断言 `src="/assets/` 同源、manifest 的 start_url/scope 等于预览域、
  图标未指向绝对域；发布前删掉 `dist/sw.js` 与 `workbox-*`（预览站不吃预缓存）。
- 冒烟：线上入口 chunk 的 sha256 必须等于本地产物；`/sw.js` 必须回落
  `text/html`；后端 CORS 必须回显 `https://canary.xiaobopisces.top`。
  四处探测统一带 `PROBE_UA`（`Mozilla/5.0 (compatible; ZVP5-Deploy/1.0)`）——
  后端盾牌对未知 UA 一律 418，用 curl 默认 UA 探测等于自找失败。
  CORS 那一步不用 `curl -f` 而是读状态码：拿到**别的** Origin 才算白名单配错
  （硬失败），一个 AC-AO 头都没有且状态非 2xx 时按「盾牌/WAF 拦了 runner IP」
  降级成 warning。
- 已知限制：Cloudflare 的 zone 级 WAF 会拦 GitHub Actions 出口 IP，预览根路径
  直接回 403，此时内容哈希与 CORS 比对整体跳过、只留 warning（本机 curl 是 200，
  属于边缘策略不是部署问题）。要真正校验内容，得在 dash 里给
  `canary.xiaobopisces.top` + Actions 出口 IP 段加 WAF skip 规则；当前 CI 用的
  Cloudflare token 没有 firewall 权限，暂未做。
- 成本控制：工作流级 `concurrency` 取消被新推送取代的旧一轮；部署 job 单独加
  `canary-worker-deploy` 组且 `cancel-in-progress: false`，避免上传被打断。

需要的仓库 Secret 只有 `CLOUDFLARE_API_TOKEN`；`CLOUDFLARE_ACCOUNT_ID` 与三个
`CANARY_*` 基址是可选的仓库 Variables，缺省值写在 workflow 里（预览站不是生产，
允许内联默认值）。细节见 `deployment/preview-worker/README.md`。

未做：canary 的 Worker 部署没有回滚动作（预览站直接重发上一次 commit 即可），
也没有把 `feat/*` 分支接进来。
