# GitHub Actions production deployment

This directory contains the server-side pieces for the root
`.github/workflows/ci.yml` production job. Pushes to `main` only run CI. A
published, non-prerelease GitHub Release triggers production deployment after
backend tests, frontend tests, Cypress, workflow checks, and dependency
vulnerability checks all pass. The release commit must also be an ancestor of
`origin/main`.

## One-time server setup

Run the bootstrap on both the frontend host and the backend host as
root. The public key must match the private key stored in the corresponding
GitHub Environment Secret.

```bash
sudo bash ./bootstrap-deploy-user.sh ./zvms4-deploy /root/zvp5-deploy.pub
```

The bootstrap creates `github-deploy`, installs
`/usr/local/sbin/zvms4-deploy`, and grants passwordless sudo only for these four
validated commands:

```text
zvms4-deploy deploy backend <sha> <deployment-id>
zvms4-deploy deploy frontend <sha> <deployment-id>
zvms4-deploy rollback backend <sha> <deployment-id>
zvms4-deploy rollback frontend <sha> <deployment-id>
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

Create an Environment named `production` and restrict it to the release-tag
pattern used by this repository. Do not add required reviewers if the approved
policy remains automatic deployment after publishing a release.

The release gate is only as strong as who can create tags and releases. Before
enabling this pipeline, add a repository ruleset that restricts creation and
deletion of `refs/tags/*` to the release managers (or admins only). Anyone with
push access must not be able to publish a tag, because publishing a
non-prerelease Release is equivalent to authorizing a production deployment.
If the policy is not fully automatic, add required reviewers to the
`production` Environment instead of relying on tag protection alone.

Keep all CI jobs visible but not required in branch protection for now. Merging
to `main` does not deploy. Before creating a release, make sure the tag points to
a commit that passed CI. Publishing the release starts the complete quality and
security pipeline again before deployment.

Environment Variables:

| Name | Meaning |
|---|---|
| `PROD_API_BASE` | Public API base ending in `/api` |
| `PROD_API_RACE_BASE` | Race API base ending in `/api`; required by the current production build |
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
3. Watch the `Deploy production` job. Its summary records the release tag,
   commit SHA, and deployment ID.
4. If required, roll back explicitly on the servers in frontend-then-backend
   order:

```bash
sudo /usr/local/sbin/zvms4-deploy rollback frontend <sha> <deployment-id>
sudo /usr/local/sbin/zvms4-deploy rollback backend <sha> <deployment-id>
```

The workflow writes staging bundles to `~/incoming` on each server. Passwords,
wallet files, `settings.py`, virtual environments, SQLite state, uploads, and
generated reports are excluded from backend archives and preserved in place.
The wrapper builds a replacement virtualenv outside the backend tree.

## Safety behavior

- Backend deploys run first; the frontend is not touched if backend health fails.
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
