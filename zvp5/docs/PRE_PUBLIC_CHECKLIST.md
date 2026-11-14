# 转公开仓库检查清单

仓库目前是**免费 plan 的私有库**，这带来一个硬限制：分支保护和 environment
的 required reviewers 都用不了（GitHub 会直接回 `Upgrade to GitHub Pro`）。
4 个 write collaborator 只要往 `main` 推一个带 `environment: production` 的
workflow，就能读到 10 条生产 secret。转公开是为了拿回这些闸门，顺带把 Actions
计费归零（公开库标准 runner 不计费）。

公开是不可逆的传播：**push 出去的内容和整条历史都进了互联网**。下面是现状、
待决项和两道必须过的关。

## 一、已经做完的（2026-11-14）

| 项 | 提交 | 说明 |
| --- | --- | --- |
| 主机 IP 脱敏 | `bf87f20` | 12 个文档/README 里 50 处真实 IP → `<BACKEND_IP>` / `<FRONTEND_IP>` / `<PROXY_IP>` / `<CLIENT_IP>`；端口、路径、服务别名保留。约定见 `REDACTION_POLICY.md` |
| 学海归档移出 | `bedf6c2` | `reference/xuehai/` 含从 `xhcore.so` 提取的第三方签名 KEY、任意 URL 签名代理、设备采集与外呼逻辑。整棵移出到 `~/zvp5-private-reference/xuehai/`，根 README 里复述的 KEY 值改为不复现 |
| 扫描配置 | `bedf6c2` / `1ec433a` | 两份 `.gitleaks*.toml` 去掉 `reference/xuehai/` 放行——这条放行正是此前扫描全绿的原因 |
| 归档不占门禁 | `bedf6c2` | `ci.yml` 的 `paths-ignore` 加 `reference/**`；纯归档改动不再跑全量门禁 |

## 二、历史里还没解决的问题

公开是把 `.git` 一起交出去，当前 `.git` 有 **39 MB**：

| 内容 | 规模 | 问题 |
| --- | --- | --- |
| `reference/xuehai/`（2 个提交） | 216 KB | **第三方签名 KEY 仍在历史里**，删当前树删不掉它 |
| `zvms4-modified-main/`（448 个提交） | 历史里约 **67 MB** blob | 已删除的旧代码线，含当时的 `.env.production`、测试夹具 |
| 全历史扫描命中 | 若干 | 都是测试夹具（假的 `api_key`、假 JWT），无生产密钥 |

所以「删文件」只解决当前树。要连历史一起干净，只有两条路：

### 方案 A：重写历史（保留 523 个提交）

```bash
git filter-repo --invert-paths \
  --path reference/xuehai --path zvms4-modified-main
```

- 好处：`git log` / blame / 提交叙事全保留，`.git` 会从 39 MB 掉到个位数。
- 代价：**所有 SHA 全变**。`/www/zvp5/.github-actions/backend.current`、现存
  release tag、部署记录里的 SHA 全部作废，得重新打 tag。远端要强制推送一次。

### 方案 B：新仓库只推当前树（推荐）

```bash
# 在一个干净目录里
git clone --depth 1 git@github.com:xiaobo2010/zvp5.git zvp5-public
cd zvp5-public
rm -rf .git && git init -b main
git add -A && git commit -m "Initial commit"
git remote add origin git@github.com:xiaobo2010/zvp5-public.git
git push -u origin main
```

- 好处：历史零负担，公开前不再需要逐 commit 审计；旧私有库原样留着当档案。
- 代价：丢掉 523 个提交的 blame。对当前阶段（v5 刚起步、文档里本来就写满了
  对账记录）影响不大。
- 注意：新仓库的 Actions Secrets / Variables / Environments **要重新配**一遍，
  workflows 里的 `github.repository` 引用如果是硬编码 `xiaobo2010/zvp5` 也要改。

> 权衡：A 保历史但会让现网部署记录失效并需要一次 force push；B 干净但要从头配
> secret。你更在意哪一头，就选哪条。

## 三、公开前必须逐项拍板

1. **`README.md:206`** 有一张生产库表快照，`users | 1781` 之类的真实行数。
   公开等于露用户规模，建议抹成 `<N>` 或整列删掉。
2. **`README.md` 学海章节的措辞**。现在写的是「原 `zvp-latest` 代码存在后门与
   漏洞」。私有仓库里这是内部结论；公开后它变成对某个学校教育平台的公开指控，
   而且这份归档本来就是你从对方产物里提取的。建议改成「含设备身份采集与任意
   URL 代理能力，已整体剥离」这种事实陈述，去掉定性词。
3. **`canary.yml` 里 Cloudflare account id 内联默认值**（`<CF_ACCOUNT_ID>…`）。
   account id 不是密钥，但公开后等于给攻击者省一步。可以改成必填变量。
4. **服务商与别名**：`zvp-backend` / `zvp-frontend` / `另一处主机` / `另一处主机`
   这些别名加上「后端 / 前端 / HK」的地理描述，能推出用的是哪家 VPS。要不要保留
   看你——删了文档就没法照着排障。
5. **LICENSE 是 MIT，署名 `XiaoBo Huang`**。公开前确认这就是你想用的协议与署名。

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
