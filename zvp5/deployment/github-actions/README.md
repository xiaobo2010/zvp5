# GitHub Actions production deployment

This directory contains the server-side pieces for the root
`.github/workflows/ci.yml` production job. Pushes to `main` only run CI. A
published, non-prerelease GitHub Release triggers production deployment after
backend tests, frontend tests, Cypress, workflow checks, and dependency
vulnerability checks all pass. The release commit must also be an ancestor of
`origin/main`.

## One-time server setup

> **改名过渡（zvms → zvp5）**：脚本已改名为 `zvp5-deploy`，安装到
> `/usr/local/sbin/zvp5-deploy`，并留一个 `zvms4-deploy` 符号链接给尚未更新的
> 调用方；sudo 策略改名 `/etc/sudoers.d/zvp5-github-actions`，旧文件在下次
> bootstrap 时自动删除（sudo 会先把符号链接解析成真实路径再匹配策略）。
> 环境变量同样双读：`ZVP5_*` 优先，未设置时回落 `ZVMS4_*`。
>
> 服务端路径默认值已指向 `/www/zvp5`，那是指向 `/www/zvms4` 的符号链接，
> 两台机器都已建好，所以这一批改动部署上去就能用，不依赖后端换名。

Run the bootstrap on both the frontend host and the backend host as
root. The public key must match the private key stored in the corresponding
GitHub Environment Secret.

> **2026-11-14 已完成**：`github-deploy` 账号、`/usr/local/sbin/zvp5-deploy`
> 与四条免密 sudo 规则已装在 `zvp-backend`（后端，<BACKEND_IP>:23456）和
> `zvp-frontend`（前端，<FRONTEND_IP>:22）。两把部署私钥只存在于 `production`
> environment 的 `BACKEND_SSH_KEY` / `FRONTEND_SSH_KEY`，本机不再保留副本。
> 轮换流程：本地 `ssh-keygen -t ed25519` 生成新对 → 覆盖远端
> `~github-deploy/.ssh/authorized_keys`（保留旧行直到新 secret 生效）→ 用
> `ssh-keyscan -p <port> <host>` 重生成 known_hosts → 更新四个 secret → 删除旧行。

```bash
sudo bash ./bootstrap-deploy-user.sh ./zvp5-deploy /root/zvp5-deploy.pub
```

The bootstrap creates `github-deploy`, installs
`/usr/local/sbin/zvp5-deploy` (plus a `zvms4-deploy` symlink for the
transition), and grants passwordless sudo only for these four
validated commands:

```text
zvp5-deploy deploy backend <sha> <deployment-id>
zvp5-deploy deploy frontend <sha> <deployment-id>
zvp5-deploy rollback backend <sha> <deployment-id>
zvp5-deploy rollback frontend <sha> <deployment-id>
```

Firewall rules must allow SSH from GitHub-hosted runners. Prefer restricting
the source ranges to GitHub's published Actions ranges rather than opening the
SSH port globally.

The server must already provide `rsync`, `curl`, `runuser`, `sha256sum`, `gzip`,
`tar`, `systemctl`, and a working Python virtualenv. Each backend release gets a
clean virtualenv under `/www/zvms4/.github-actions/venvs/`; the wrapper switches
`/www/zvms4/backend/.venv` as a directory and restores the previous environment
on rollback. Frontend files are owned by `www:www` by default; override with
`ZVMS4_FRONTEND_OWNER` only if the target host uses a different nginx user.

## GitHub Environment configuration

`production` requires approval from `xiaobo2010` and disallows administrator
bypass. Keep production SSH credentials in protected environments and restrict
deployments to the repository's release-tag pattern. Older refs may still use
`production-auto`, which has the same approval requirements.

Repository collaborators with write access can push to `main` when the active
branch rules permit direct updates. Keep deletion and force-push protection.
Before creating a release, make sure the tag points to a commit that passed CI.
Publishing the release starts the complete quality and security pipeline again.

Environment Variables:

| Name | Meaning |
|---|---|
| `PROD_API_BASE` | Canonical same-origin `/api`, or an absolute HTTPS API base ending in `/api` for a split-origin rollback |
| `PROD_API_RACE_BASE` | Race API base ending in `/api`; required by the current production build |
| `PROD_STATIC_BASE` | High-bandwidth static asset origin ending in `/`; required, no fallback — an unset variable fails the preflight instead of silently building against a stale origin |
| `PROD_FRONTEND_HEALTH_URL` | Public frontend root URL |

Production API hosts are runtime configuration and are not stored in the
workflow. The deployment job validates both configured bases, preflights their
`/health` endpoints, verifies their injection into the built JavaScript, and
checks that the generated preconnect origins are present. Changing a production
host therefore requires only the Environment variables and matching DNS/TLS/API
configuration.

Environment Secrets:

| Name | Meaning |
|---|---|
| `BACKEND_SSH_HOST` | backend API host |
| `BACKEND_SSH_PORT` | backend SSH port |
| `BACKEND_SSH_USER` | `github-deploy` |
| `BACKEND_SSH_KEY` | Private key for the backend |
| `BACKEND_KNOWN_HOSTS` | Pinned host key for the backend |
| `FRONTEND_SSH_HOST` | frontend host |
| `FRONTEND_SSH_PORT` | frontend SSH port |
| `FRONTEND_SSH_USER` | `github-deploy` |
| `FRONTEND_SSH_KEY` | Private key for the frontend |
| `FRONTEND_KNOWN_HOSTS` | Pinned host key for the frontend |

## Release and rollback flow

1. Merge and verify CI on `main`.
2. Create a GitHub Release from a commit on `main` and publish it as a normal
   release. Saving a draft does not deploy; publishing a release marked as a
   prerelease is rejected by the deployment job. Manual workflow dispatch runs
   CI only and cannot bypass the release gate.
3. `xiaobo2010` reviews the pending `production` deployment in the Actions run.
   After approval, the `Deploy production` job records the release tag, commit
   SHA, and deployment ID in its summary.
4. If required, roll back explicitly on the servers in frontend-then-backend
   order:

```bash
sudo /usr/local/sbin/zvp5-deploy rollback frontend <sha> <deployment-id>
sudo /usr/local/sbin/zvp5-deploy rollback backend <sha> <deployment-id>
```

The workflow writes staging bundles to `~/incoming` on each server. Passwords,
wallet files, `settings.py`, virtual environments, SQLite state, uploads, and
generated reports are excluded from backend archives and preserved in place.
The wrapper builds a replacement virtualenv outside the backend tree.

## Safety behavior

- Backend deploys run first; the frontend is not touched if backend health fails.
- The frontend bundle is deployed to BOTH machines: the frontend host serves
  `/assets` (the high-bandwidth static origin), while the backend host serves
  `index.html` for the public site. The two copies must match, otherwise the
  entry page references asset hashes that 404 on the static origin and the site
  renders blank (2026-11-20 production incident). The post-deploy health check
  therefore fetches an entry asset URL extracted from the served `index.html`,
  not just the page root.
- Both components are backed up under
  `/www/zvms4/backups/github-actions/` before activation. Backup names include
  the release SHA and deployment ID; the wrapper keeps the four newest backups
  and four newest prior backend virtualenvs.
- Re-running the same release SHA is a no-op when that component is already
  active, so a retry cannot overwrite the snapshot of the previously stable
  release.
- A failed backend restart/health check rolls back locally. A later public
  frontend failure causes the workflow to roll back frontend and backend in
  reverse order.
- Rollback restores both application files and the exact previous backend
  virtualenv; it does not reinstall requirements into a shared mutable venv.
- Frontend archives are checksum-verified, precompressed with `gzip -9k`, and
  activated with a directory swap.
- Both archives are size-capped and extracted with `--no-same-owner
  --no-same-permissions`, so an archive delivered over the deploy account's own
  channel cannot plant a setuid-root binary. This does not replace host
  hardening: anyone holding a deploy key still controls deployed content.
- Successful deploys remove their own files from `~/incoming`.
- Every invocation takes an exclusive `flock` on
  `/www/zvms4/.github-actions/deploy.lock` before touching anything, so a manual
  run and a CI retry cannot swap the same tree concurrently. A waiting run aborts
  after `ZVMS4_DEPLOY_LOCK_TIMEOUT` seconds (default 600).
- The built frontend is rejected unless both configured API bases are valid,
  their health endpoints are reachable, and both values appear in the bundle and
  generated preconnect links.
- Database DDL is never executed. The workflow compares the release with the
  previous reachable release tag and warns if DDL changed anywhere in that
  complete range.
- Deployment notifications use only GitHub Actions and the `production`
  Environment deployment history.

## Local wrapper test

The test uses temporary directories and fake `systemctl`/`curl` commands:

```bash
bash ./test-deploy-wrapper.sh
```

### 生产主题静态分发

**警告：** `production` 和 `production-auto` 的 `PROD_STATIC_BASE` 应保持 `https://zvpapi.xiaobopisces.top/`。改成 `/` 或页面域会让资源回流页面服务器。`PROD_API_BASE=/api` 是独立的同源 API 配置。子主题 HTML 和 hash 路由挂在页面域 `/themes/<id>/`，编译资源走静态域已有 CORS 的 `/assets/themes/<id>/`。两个主机必须部署同一份产物。发布流程检查入口脚本的域、版本、MIME 和 CORS，失败时进入回滚。
