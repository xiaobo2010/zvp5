# 转公开仓库检查清单

仓库目前是**免费 plan 的私有库**，这带来一个硬限制：分支保护和 environment
的 required reviewers 都用不了（GitHub 会直接回 `Upgrade to GitHub Pro`）。
4 个 write collaborator 只要往 `main` 推一个带 `environment: production` 的
workflow，就能读到 10 条生产 secret。转公开是为了拿回这些闸门，顺带把 Actions
计费归零（公开库标准 runner 不计费）。

公开是不可逆的传播：**push 出去的内容和整条历史都进了互联网**。下面是现状、
待决项和两道必须过的关。

## 一、已经做完的（2026-11-14）

> 2026-11-14 晚做过一次全历史重写，下面这些提交的 SHA 都已变化；表中的
> 7 位短 SHA 只用于指认「哪一次改动」，不再能在远端解析。旧 SHA 对应关系只
> 保存在本机备份 bundle 里（见 §二）。

| 项 | 提交 | 说明 |
| --- | --- | --- |
| 主机 IP 脱敏 | `bf87f20` | 12 个文档/README 里 50 处真实 IP → `<BACKEND_IP>` / `<FRONTEND_IP>` / `<CLIENT_IP>`；端口、路径、服务别名保留。约定见 `REDACTION_POLICY.md` |
| 学海归档移出 | `bedf6c2` | `reference/xuehai/` 含从 `xhcore.so` 提取的第三方签名 KEY、任意 URL 签名代理、设备采集与外呼逻辑。整棵移出到 `~/zvp5-private-reference/xuehai/`，根 README 里复述的 KEY 值改为不复现 |
| 扫描配置 | `bedf6c2` / `1ec433a` | 两份 `.gitleaks*.toml` 去掉 `reference/xuehai/` 放行——这条放行正是此前扫描全绿的原因 |
| 归档不占门禁 | `bedf6c2` | `ci.yml` 的 `paths-ignore` 加 `reference/**`；纯归档改动不再跑全量门禁 |

## 二、历史重写（2026-11-14 已完成）

`git filter-repo` 跑了一遍全历史重写，`main` / `v5-canary` / `feat/yg-redesign-v2` /
5 个 dependabot 分支一起强推。结果：

| 项 | 重写前 | 重写后 |
| --- | --- | --- |
| `main` 提交数 | 534 | **534**（一个没少；重写只是换 SHA，不丢提交） |
| `.git` 体积 | 39 MB | **19 MB** |
| 提交信息里的服务商/机房词 | 39 条 | 0 |
| 树内容里的账号 id / 别名 / 地理词 / 真实 IP | 若干 | 0（全历史逐 commit 扫过） |

重写规则（同一份映射同时作用于文件内容与提交信息）：

- 后端机、前端机的服务商别名 → `zvp-backend` / `zvp-frontend`；
- 其余主机（中继、备用、自有）一律不点名 → 「另一处主机 / 另一处境外出口」；
- 机房所在国家/城市（含英文缩写）→ 「后端 / 前端」；数据库所在区域 → 「数据库侧」；
- Cloudflare account id → `<CF_ACCOUNT_ID>`，DNS record id → `<CF_RECORD_ID>`；
- 真实主机 IP → `<BACKEND_IP>` / `<FRONTEND_IP>` / `<CLIENT_IP>`；
- 三个文件名带地理/服务商信息的文档，整条历史里一起改名。

> 这份清单本身也不再复述旧值——把旧别名原样写在这里，等于换个地方泄露一次。

**没有动**（都是明确决定）：`reference/xuehai/` 里的第三方签名 KEY 仍在历史里——
它要留着做设备/身份追踪，代价就是公开后它一并可见。真要抹掉，加一条
`--replace-text` 规则再跑一次同样的流程即可。

备份：`~/zvp5-pre-rewrite-20261114.bundle`（全 refs 的 bundle，含旧 SHA 的全部
对象）。要回滚就在干净目录 `git clone <bundle>` 后重新强推。

### 还没清掉的：GitHub 侧旧对象

强推只是把分支指向新历史，**旧对象仍在 GitHub 仓库里，按 SHA 仍然能访问**，
GitHub 自己 GC 之前一直在。所以：

- 若只是「别人不容易翻到」→ 现状够用；
- 若要真的断掉旧 SHA 的可达性 → 只有两条路：让 GitHub Support 回收，或者按
  方案 B 新建仓库只推当前树（新仓库没有旧对象）。转公开前建议走后者：把重写后
  的 history 推到新仓库，旧私有库原样留档。

## 三、逐项裁定（2026-11-14）

| # | 项 | 结论 |
| --- | --- | --- |
| 1 | `README.md:206` 的 `users \| 1781` 行数 | **不改**（用户裁定：用户行数无所谓） |
| 2 | 学海相关措辞 | **已软化**：去掉「后门 / 漏洞」等定性词，改成「含设备标识采集、任意 URL 代签与外部跳转等未经审计能力」的事实陈述（`docs/history/DEVELOPMENT_LOG.md` / `docs/history/MAINLINE_DEV_NOTES.md` / `docs/history/MODULE_MIGRATION_AUDIT.md` / `REFERENCE_TREES_ALIGNMENT.md`） |
| 3 | Cloudflare account id | **已从当前树彻底清除**：`canary.yml` 改为只读 `vars.CLOUDFLARE_ACCOUNT_ID`（值放在 canary environment）；文档里的 account id 与 DNS `record_id` 全部换 `<CF_ACCOUNT_ID>` / `<CF_RECORD_ID>` |
| 4 | 服务商别名与机房地理 | **已处理**：主机别名统一成 `zvp-backend` / `zvp-frontend`，地理描述统一成「后端 / 前端」；第三方主机不再点名，只按「另一处主机 / 另一处境外出口」描述。当前树只剩前后端两台的身份 |
| 5 | LICENSE（MIT，署名 `XiaoBo Huang`） | **保留** |

### 关于 account id 的历史残留

当前树已清零（文档里也不再留任何前缀），但公开是把 `.git` 一起交出去，那串
32 位账号标识仍在 **5 个历史提交**里：

```
b5a0802 2026-11-05 chore(deploy): 忽略 worktrees/，补齐前端分支预览 Worker 的部署说明
ceb7c2d 2026-11-08 docs: ZVP5 大修总账
55095a6 2026-11-14 ci: 收口发布链路并新增 canary 前端预览部署
dc9438a 2026-11-14 ci(canary): v5-canary 推送跑门禁并把前端预览发到 Cloudflare Worker
89d9e0b 2026-11-14 docs: 转公开仓库检查清单
```

要连历史一起清，只能用 `git filter-repo --replace-text` 做字符串替换（比删路径温和，
但照样改 SHA）：`b5a0802` 之后的所有提交换名，`backend.current` 里的旧 SHA 悬空。
DNS `record_id` 在 3 个提交里，同批处理。

## 四、公开后立刻做（这才是转公开的目的）

1. **ruleset `main`**：require PR + 1 approval、required status checks
   （`Backend quality` / `Frontend quality` / `Workflow and secret checks` /
   `Frontend Cypress E2E` / `Dependency vulnerability checks`）、block force push、
   block deletion、require linear history。
2. **ruleset `v5-canary`**：只加 block force push + block deletion。这个分支要
   允许直推，否则 canary 自动部署失去意义。
3. **`production` environment**：required reviewers = 你自己。这是生产 secret 的
   真闸门，私有免费库开不了，公开后就能开。
4. **复核 collaborator**：write 权限等于「能提 PR 改 workflow」。4 个人（albxy、
   wzxundesigned、neiworldfirst1314-cell、Chenjunyu2010）留几个、给什么角色，
   公开后重新过一遍。
5. 跑一次真实的 release，把生产部署链路（构建 → 上传两机 → backend deploy →
   前端 → 健康检查 → 失败回滚）完整走通一次再宣告定稿。所需 4 个 Variables 与
   10 条 Secrets 现在都已就位。

## 五、公开后的副作用

- **Actions 计费归零**：标准 runner 上公开库不扣额度。`paths-ignore`、只跑前端
  的 canary 通道这些省分钟的措施可以保留（省时间），但不再是硬约束。
- **fork PR 不会拿到 secret**：`pull_request` 事件在 fork 上不注入 secret，
  `GITHUB_TOKEN` 只读；本仓库没有 `pull_request_target` / `workflow_run`，
  runner 全是 GitHub 托管，公开库最经典的「fork PR 打穿自托管 runner」不适用。
- **搜索可见**：`reference/zvp5-gitlab/` 这类未完成设计稿会被人翻到，酌情删。

## 六、2026-11-15 第二轮清理（全历史重写）

| 项 | 处置 |
| --- | --- |
| 真实学生姓名索引表 | `docs/history/LEGACY_CODE_NOTES.md` 的 §6 索引表与「来源」说明从当前树删除；该文件的**全部历史版本**已从重写后的历史中移除，脱敏版在重写后作为新提交重新加入 |
| 事故复盘 `docs/ISSUE_2026-11-03_AI_CREDENTIAL_UNREADABLE.md` | 当前树与历史一并删除，`zvp5.service` 与 `tools/setup_local.py` 里的引用改为中性表述 |
| 内网地址 / 权威 NS / 后端公网 IP | 当前树写 `<INTERNAL_HOST>` / `<CF_NAMESERVER>` / `<BACKEND_IP>`；历史与代码/测试统一换成 TEST-NET 文档地址（`192.0.2.x`）与 `ns1.example.com`，保证任何按 IP 解析的断言仍然成立 |
| `docs/history/DEVELOPMENT_LOG.md` §八「学海 API 调查报告」 | 整节移出仓库，只保留结论；全文存档在仓库外 |
| 许可 | 新增 `NOTICE`：MIT 覆盖范围、上游 `zvms/zvms4-*` 署名、不再分发内容的说明；README 补「十一、许可」 |
| `.gitignore` | 根补 `.env*` / `*.pem` / `*.key` / `*.p12` / `aes_key.txt` / `credentials/`；前端补 `.env` 规则（此前完全没有） |
| 用户裁定不处理 | `reference/xuehai/` 在历史中的第三方签名材料按决定保留；公有域名（`*.xiaobopisces.top`、`albwxy.fun`）不清，CI 里仍是功能默认值 |

## 七、正式转 public 与闸门（2026-11-15 已执行）

| 项 | 结果 |
| --- | --- |
| 可见性 | `private` → `public` |
| ruleset `main` | deletion / non_fast_forward / required_linear_history / require PR + 1 approval / 5 项 required checks；bypass 只给 `xiaobo2010`（always） |
| ruleset `v5-canary` | deletion / non_fast_forward（保留直推，canary 预览部署需要） |
| `production` environment | branch policy `main` + required reviewers = `xiaobo2010` |
| `canary` environment | branch policy `v5-canary` |
| 密钥扫描 | secret scanning 与 push protection 开启；Dependabot security updates 与 vulnerability alerts 开启 |
| 协作者 | `Chenjunyu2010` 降权：个人仓库不支持 triage 角色（API 静默 no-op 且不报错），改为移出协作者——公开库下等同只读；其余 3 个 write 保留 |
| 历史 Actions 运行 | 重写前（2026-11-15T03:30Z 之前）的 run 全部删除，避免旧部署日志随公开可见 |
| 新增 `Docs guard` | 改 `*.md` / `*.txt` 时跑一条几十秒的任务，拦截写回内网地址 / OCI 区域 / 权威 NS / 后端公网 IP；**不进 required**（只在文档改动时触发，设成 required 会让代码 PR 永远 pending） |

> 注：`ci.yml` 的 `paths-ignore` 只挂在 `push` 触发器上；`pull_request` 没有路径过滤，
> 因此文档 PR 照样跑完整五项 required checks，不存在「文档 PR 卡在 pending」的问题。
