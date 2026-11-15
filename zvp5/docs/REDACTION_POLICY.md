# 文档脱敏约定

本仓库后续计划转为公开仓库，凡是涉及真实基础设施的文档，一律写占位符，
真实值只保留在私人笔记、`~/.ssh/config` 和服务器上。

## 占位符

| 占位符 | 含义 | 出现场景 |
| --- | --- | --- |
| `<BACKEND_IP>` | 后端/API 所在机（别名 `zvp-backend`） | 部署记录、排障日志 |
| `<FRONTEND_IP>` | 前端源站（别名 `zvp-frontend`） | 反代配置、`ssh-keyscan` 示例 |
| `<CLIENT_IP>` | 真实访客/终端 IP | access.log 摘录 |
| `<CF_ACCOUNT_ID>` | Cloudflare account id | Worker 部署命令、wrangler 说明 |
| `<CF_RECORD_ID>` | Cloudflare 单条 DNS 记录的 record id | DNS 切换/回滚记录 |

端口、路径、目录结构、服务别名可以保留——它们离开 IP 之后不构成可探测的入口，
但删掉会让文档失去可操作性（比如 `sshd` 不在 22、`/www/zvp5` 的真实布局）。

域名同样保留：`zvp.xiaobopisces.top` 之类的记录本来就是公开 DNS 可见的，
藏起来只会让文档自相矛盾。

账号级标识（Cloudflare account id、DNS record id）不进文档：它们虽然不能单独
当凭据用，但能省掉攻击者定位账号的一步。account id 只配在 canary environment
的 `CLOUDFLARE_ACCOUNT_ID` 变量里，本机副本在
`~/.config/opencode/credentials/cf-account-id`。

## 主机命名

只暴露两台机器的身份，其余主机不点名：

| 角色 | 写法 |
| --- | --- |
| 后端 / API 所在机 | `zvp-backend`，正文里简称「后端」 |
| 前端源站 | `zvp-frontend`，正文里简称「前端」 |
| 其余主机（中继、备用、自有…） | 「另一处主机」或「另一处境外出口」，不写别名、不写地理 |

机房所在国家/城市同样不写：它和主机别名组合起来能直接对到服务商。

## 提交前自查

```bash
# 真实 IP 数量应为 0（RFC5737 文档地址 203.0.113.x / 198.51.100.x 除外）
git grep -nE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' -- '*.md' '*.yml' \
  | grep -vE '127\.0\.0\.1|0\.0\.0\.0|203\.0\.113|198\.51\.100|169\.254'
```

2026-11-14 已经用 `git filter-repo` 把整条历史洗过一遍：文件内容与提交信息里的
真实 IP、服务商别名、机房地理、Cloudflare 账号标识全部替换成占位符，531 个提交
一个没少（见 `PRE_PUBLIC_CHECKLIST.md` §二）。新增提交自然也要守住上面的约定。

## 已移出的内容

| 内容 | 处理 | 原因 |
| --- | --- | --- |
| `reference/xuehai/` | 2026-11-14 移出仓库，本机保留在 `~/zvp5-private-reference/xuehai/` | 含从 `xhcore.so` 提取的 64 字符签名 KEY、任意 URL 签名代理、设备身份采集与外呼逻辑；公开等于把伪造学海 API 请求的能力交出去 |

> KEY 仍需要保留（后续设备/身份追踪要用），只是不进仓库：见 `~/zvp5-private-reference/xuehai/`。
> 注意历史提交里仍有这棵归档，真要公开得重写那 2 个提交，否则等于把 KEY 发出去。

根 `README.md` 里原本复述过那个 KEY 的值，已改成「只记结论、不复现值」。
`.gitleaks.toml` 中针对 `reference/xuehai/` 的放行条目同步删除——万一有人把它复制
回来，密钥扫描会直接拦住。
