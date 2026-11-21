# 贡献指南

感谢你愿意为 ZVP5（镇海中学义工管理平台）出力。本文件说明本地环境、提交规范、
CI 门禁和评审流程。**动手前请先读完本页。**

## 一、先读什么

| 你的角色 | 必读 |
| --- | --- |
| 所有人 | 本文件 + [`docs/README.md`](docs/README.md)（文档地图） |
| 后端 | [`zvp5/zvp5-backend-python/README.md`](zvp5/zvp5-backend-python/README.md)、[`zvp5/docs/API_STRUCTURE.md`](zvp5/docs/API_STRUCTURE.md) |
| 前端 | [`zvp5/zvp5-frontend/README.md`](zvp5/zvp5-frontend/README.md)、根目录 [`前端约定/README.md`](前端约定/README.md) |
| 运维 / 部署 | [`zvp5/deployment/github-actions/README.md`](zvp5/deployment/github-actions/README.md) |
| 提交者 | [`zvp5/docs/REDACTION_POLICY.md`](zvp5/docs/REDACTION_POLICY.md)（脱敏规范，**必读**） |

在线文档站：<https://xiaobo2010.github.io/zvp-docs/>（独立仓库
[xiaobo2010/zvp-docs](https://github.com/xiaobo2010/zvp-docs)）。

## 二、本地环境

### 前置

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11+（CI 用 3.13） | 后端运行时 |
| Node.js | 24（`.node-version` = 24.19.0） | 前端构建 |
| pnpm | 10.34.5（由 `packageManager` 固定） | 前端包管理，**不要用 npm/yarn** |
| Oracle Autonomous Database | —— | 唯一运行时数据库，需要 wallet |

### 后端

```bash
cd zvp5/zvp5-backend-python
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/setup_local.py     # 生成 settings.py / RSA 密钥 / aes_key.txt（已存在则不覆盖）
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

随后在 `settings.py` 里填 `ORACLE_USER` / `ORACLE_PASSWORD` 以及所选实例的 DSN、
wallet 路径与 wallet 密码。密钥、配置、wallet、`.env` 全部不入库。

`GET /api/health` 在数据库不可用时返回 503。盾牌中间件要求请求 `User-Agent`
含 `Mozilla`，用 curl 验收时记得带上。

### 前端

```bash
cd zvp5/zvp5-frontend
pnpm install --frozen-lockfile
pnpm dev            # http://localhost:5173
```

`predev` / `prebuild` 会自动跑 `design:sync`，把根目录 `前端约定/css` 同步到
`src/assets/styles/yg/`。**样式的事实在 `前端约定/css`，不在 `src/` 里**。

## 三、测试与检查

改动落地前请本地跑通对应门禁：

```bash
# 后端
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q routers services util db typings
.venv/bin/python -m ruff check .        # ruff 非运行依赖，需自行安装

# 前端
pnpm exec vitest run
pnpm lint
pnpm type-check
pnpm build
pnpm exec cypress install && pnpm test:e2e   # 浏览器二进制不随依赖安装
```

CI 的五类门禁（`Backend quality` / `Frontend quality` / `Frontend Cypress E2E`
/ `Workflow and secret checks` / `Dependency vulnerability checks`）在 PR 上全部
必过。改 `.md` / `.txt` 时另有一条几十秒的 `Docs guard`，用来拦截内网地址、
OCI 区域、权威 NS 等基础设施标识被写回文档。

## 四、提交与分支

- 分支：`feat/…`、`fix/…`、`docs/…`；`main` 走 PR 且需 1 个 approval，
  禁止 force push 与删除分支。
- 提交信息用中文，格式沿用仓库现状：`type(scope): 摘要`，例如
  `fix(web): 修复个人主页低 GPU 档流光动画掉帧`。
- 一个 PR 只做一件事。改动了数据库 DDL 就在 PR 模板里勾选，并单独说明迁移
  步骤——**DDL 永不自动执行**。
- 不要提交 `settings.py`、`aes_key.txt`、`*.pem`、wallet、`.env`、`dist/`、
  `node_modules/`。`.gitignore` 已覆盖，但 `git add -A` 前请再确认一次。

## 五、脱敏红线（公开仓库）

本仓库是 **public**。任何 PR 都不得引入：

- 真实 IP、内网地址、主机别名、机房/区域信息；
- 数据库账号密码、wallet 密码、API Key、SSH 私钥路径；
- 真实学生姓名、学号、班级名单等个人信息；
- 第三方平台接口的逆向分析材料与签名密钥。

占位符写法见 [`zvp5/docs/REDACTION_POLICY.md`](zvp5/docs/REDACTION_POLICY.md)。
`Docs guard` 与 Gitleaks 会拦一部分，但别依赖工具兜底。

## 六、评审与合并

- PR 模板里有「变更说明 / 验证 / 发布影响 / 关联 Issue」四段，请如实填写。
- 涉及 `.github/`、两份 `.gitleaks*.toml`、`zvp5/deployment/` 的改动由
  仓库所有者 review（见 `.github/CODEOWNERS`）。
- 改动 `.github/workflows/` 等于能改发布链路，评审时按高风险对待。

## 七、文档约定

- 新增设计文档放 `zvp5/docs/`，并在 `zvp5/docs/README.md` 的索引表登记。
- 过程记录（发布、事故、审计）文件名带日期前缀，如
  `DEPLOY_2026-11-20_TOPIC.md`，归入索引表的「历史」组。
- 改行为就要改文档：后端契约变更同步更新
  [zvp-docs](https://github.com/xiaobo2010/zvp-docs) 站点对应页面。

## 八、行为准则

参与即同意 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。发现安全问题请走
[`SECURITY.md`](SECURITY.md) 的私密渠道，不要开公开 Issue。

## 九、许可

代码以 MIT 发布（见 [`LICENSE`](LICENSE)）。本仓库派生自上游 `zvms` 项目，
来源与署名见 [`NOTICE`](NOTICE)。
