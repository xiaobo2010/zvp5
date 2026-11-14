#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/zvp5-deploy"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

# 这里的 ZVMS4_* 是故意的：脚本里那段「旧名映射成新名」的过渡逻辑必须被覆盖到。
# 只有在两台机器的 env 全部换成 ZVP5_*、回落段删掉之后，才把它们改成 ZVP5_*。
export ZVMS4_DEPLOY_ROOT="$TEST_ROOT/www"
export ZVMS4_DEPLOY_INCOMING="$TEST_ROOT/incoming"
export ZVP5_DEPLOY_SERVICE="zvms4-test"
export ZVMS4_DEPLOY_HEALTH_ATTEMPTS=1
export ZVMS4_DEPLOY_HEALTH_SLEEP=0
export ZVMS4_SKIP_CHOWN=1
export ZVMS4_SKIP_CHMOD=1
REAL_MV="$(command -v mv)"

mkdir -p \
  "$ZVMS4_DEPLOY_ROOT/frontend-dist" \
  "$ZVMS4_DEPLOY_ROOT/backend/.venv/bin" \
  "$ZVMS4_DEPLOY_INCOMING" \
  "$TEST_ROOT/bin" \
  "$TEST_ROOT/source/frontend" \
  "$TEST_ROOT/source/backend"

printf '<html>old-frontend</html>\n' > "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"
printf 'old-package==1\n' > "$ZVMS4_DEPLOY_ROOT/backend/requirements.txt"
printf 'old backend\n' > "$ZVMS4_DEPLOY_ROOT/backend/app.py"
printf 'old-env\n' > "$ZVMS4_DEPLOY_ROOT/backend/.venv/old-env-marker"
mkdir -p "$ZVMS4_DEPLOY_ROOT/backend/adb-wallet" "$ZVMS4_DEPLOY_ROOT/backend/.ssh"
printf 'settings\n' > "$ZVMS4_DEPLOY_ROOT/backend/settings.py"
printf 'settings backup\n' > "$ZVMS4_DEPLOY_ROOT/backend/settings.py.bak"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/aes_key.txt"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/ssh_key"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/id_rsa"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/id_ed25519"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/cwallet.sso"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/tnsnames.ora"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/adb-wallet/ewallet.pem"
printf 'secret\n' > "$ZVMS4_DEPLOY_ROOT/backend/.ssh/id_rsa"
printf '#!/usr/bin/env bash\nexit 0\n' > "$ZVMS4_DEPLOY_ROOT/backend/.venv/bin/pip"
cat > "$ZVMS4_DEPLOY_ROOT/backend/.venv/bin/python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "-m" && "$2" == "venv" ]] || exit 0
target="${@: -1}"
mkdir -p "$target/bin"
printf 'new-env\n' > "$target/new-env-marker"
cat > "$target/bin/pip" <<'PIP'
#!/usr/bin/env bash
if [[ -n "${ZVMS4_TEST_PIP_LOG:-}" ]]; then
  printf '%s\n' "$@" >> "$ZVMS4_TEST_PIP_LOG"
fi
exit 0
PIP
printf '#!/usr/bin/env bash\nexit 0\n' > "$target/bin/python"
chmod +x "$target/bin/pip" "$target/bin/python"
EOF
chmod +x "$ZVMS4_DEPLOY_ROOT/backend/.venv/bin/pip" "$ZVMS4_DEPLOY_ROOT/backend/.venv/bin/python"

cat > "$TEST_ROOT/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$TEST_ROOT/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$TEST_ROOT/bin/rsync" <<'EOF'
#!/usr/bin/env bash
if [[ -n "${ZVMS4_TEST_RSYNC_LOG:-}" ]]; then
  printf '%s\n' "$@" >> "$ZVMS4_TEST_RSYNC_LOG"
fi
source_dir="${@: -2:1}"
target_dir="${@: -1}"
cp -a "$source_dir"/. "$target_dir"/
EOF
cat > "$TEST_ROOT/bin/chown" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TEST_ROOT/bin/systemctl" "$TEST_ROOT/bin/curl" "$TEST_ROOT/bin/rsync" "$TEST_ROOT/bin/chown"
export ZVMS4_SYSTEMCTL="$TEST_ROOT/bin/systemctl"
export ZVMS4_CURL="$TEST_ROOT/bin/curl"
export PATH="$TEST_ROOT/bin:$PATH"
export ZVMS4_DEPLOY_PATH="$PATH"
export ZVMS4_TEST_RSYNC_LOG="$TEST_ROOT/rsync.args"

REV_A=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
REV_B=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
REV_C=cccccccccccccccccccccccccccccccccccccccc
ID_A=run-100-1
ID_A_RETRY=run-100-2
ID_B=run-101-1
ID_C=run-102-1
export ZVMS4_TEST_PIP_LOG="$TEST_ROOT/pip.args"

archive() {
  local component="$1" revision="$2" source="$3"
  local output="$ZVMS4_DEPLOY_INCOMING/$component-$revision.tar.gz"
  tar -C "$source" -czf "$output" .
  sha256sum "$output" | awk '{print $1}' > "$output.sha256"
}

assert_file() {
  local expected="$1" path="$2"
  grep -q "$expected" "$path" || {
    echo "Assertion failed: $path does not contain $expected" >&2
    exit 1
  }
}

printf '<html>new-frontend</html>\n' > "$TEST_ROOT/source/frontend/index.html"
archive frontend "$REV_A" "$TEST_ROOT/source/frontend"
bash "$WRAPPER" deploy frontend "$REV_A" "$ID_A"
assert_file 'new-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"
frontend_backup="$ZVMS4_DEPLOY_ROOT/backups/github-actions/frontend-$REV_A-$ID_A.tar.gz"
[[ -f "$frontend_backup" ]]
bash "$WRAPPER" deploy frontend "$REV_A" "$ID_A_RETRY"
[[ ! -e "$ZVMS4_DEPLOY_ROOT/backups/github-actions/frontend-$REV_A-$ID_A_RETRY.tar.gz" ]]
bash "$WRAPPER" rollback frontend "$REV_A" "$ID_A"
assert_file 'old-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"
bash "$WRAPPER" rollback frontend "$REV_A" "$ID_A"
[[ ! -e "$ZVMS4_DEPLOY_ROOT/.github-actions/frontend-$REV_A-$ID_A.failed" ]]

printf 'new-package==2\n' > "$TEST_ROOT/source/backend/requirements.txt"
printf 'new backend\n' > "$TEST_ROOT/source/backend/app.py"
archive backend "$REV_A" "$TEST_ROOT/source/backend"
bash "$WRAPPER" deploy backend "$REV_A" "$ID_A"
assert_file 'new-package==2' "$ZVMS4_DEPLOY_ROOT/backend/requirements.txt"
[[ -d "$ZVMS4_DEPLOY_ROOT/backend/.venv" && ! -L "$ZVMS4_DEPLOY_ROOT/backend/.venv" ]]
assert_file 'new-env' "$ZVMS4_DEPLOY_ROOT/backend/.venv/new-env-marker"
[[ -f "$ZVMS4_DEPLOY_ROOT/.github-actions/venvs/backend-previous-$REV_A-$ID_A/old-env-marker" ]]
grep -Fx -- '-r' "$ZVMS4_TEST_PIP_LOG" >/dev/null
grep -F '/requirements.txt' "$ZVMS4_TEST_PIP_LOG" >/dev/null
bash "$WRAPPER" deploy backend "$REV_A" "$ID_A_RETRY"
[[ ! -e "$ZVMS4_DEPLOY_ROOT/backups/github-actions/backend-$REV_A-$ID_A_RETRY.tar.gz" ]]
for secret in \
  settings.py settings.py.bak aes_key.txt ssh_key id_rsa id_ed25519 \
  cwallet.sso tnsnames.ora adb-wallet/ewallet.pem .ssh/id_rsa; do
  [[ -f "$ZVMS4_DEPLOY_ROOT/backend/$secret" ]] || {
    echo "Assertion failed: backend deploy removed $secret" >&2
    exit 1
  }
done
backend_backup="$ZVMS4_DEPLOY_ROOT/backups/github-actions/backend-$REV_A-$ID_A.tar.gz"
if tar -tzf "$backend_backup" | grep -Eq '(^|/)(settings\.py[^/]*|aes_key\.txt|ssh_key|id_rsa|id_ed25519|cwallet\.sso|tnsnames\.ora|.*\.pem|adb-wallet|\.ssh)(/|$)'; then
  echo 'Assertion failed: backend backup contains protected credentials' >&2
  exit 1
fi
for pattern in 'settings.py*' 'id_rsa*' 'id_ed25519*' '.ssh' 'adb-wallet' 'cwallet*' 'ewallet*' 'tnsnames*'; do
  grep -Fx -- "--exclude=$pattern" "$ZVMS4_TEST_RSYNC_LOG" >/dev/null || {
    echo "Assertion failed: rsync is missing exclude $pattern" >&2
    exit 1
  }
done
pip_lines_before_rollback="$(wc -l < "$ZVMS4_TEST_PIP_LOG")"
bash "$WRAPPER" rollback backend "$REV_A" "$ID_A"
assert_file 'old-package==1' "$ZVMS4_DEPLOY_ROOT/backend/requirements.txt"
[[ -d "$ZVMS4_DEPLOY_ROOT/backend/.venv" && ! -L "$ZVMS4_DEPLOY_ROOT/backend/.venv" ]]
assert_file 'old-env' "$ZVMS4_DEPLOY_ROOT/backend/.venv/old-env-marker"
[[ "$(wc -l < "$ZVMS4_TEST_PIP_LOG")" -eq "$pip_lines_before_rollback" ]]
bash "$WRAPPER" rollback backend "$REV_A" "$ID_A"

printf 'broken-package==3\n' > "$TEST_ROOT/source/backend/requirements.txt"
archive backend "$REV_B" "$TEST_ROOT/source/backend"
cat > "$TEST_ROOT/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 22
EOF
chmod +x "$TEST_ROOT/bin/curl"
export ZVMS4_SKIP_ROLLBACK_HEALTH=1
if bash "$WRAPPER" deploy backend "$REV_B" "$ID_B"; then
  echo "Expected failed health check to abort deployment" >&2
  exit 1
fi
assert_file 'old-package==1' "$ZVMS4_DEPLOY_ROOT/backend/requirements.txt"
[[ ! -e "$ZVMS4_DEPLOY_ROOT/.github-actions/backend.current" ]]
[[ ! -e "$ZVMS4_DEPLOY_ROOT/.github-actions/venvs/backend-$REV_B-$ID_B" ]]
bash "$WRAPPER" rollback backend "$REV_B" "$ID_B"

printf '<html>interrupted-frontend</html>\n' > "$TEST_ROOT/source/frontend/index.html"
archive frontend "$REV_C" "$TEST_ROOT/source/frontend"
cat > "$TEST_ROOT/bin/mv" <<'EOF'
#!/usr/bin/env bash
if [[ "${ZVMS4_TEST_FAIL_FRONTEND_ACTIVATION:-0}" == "1" && \
      "$1" == "${ZVMS4_TEST_FRONTEND_STAGING:-}" && \
      "$2" == "${ZVMS4_TEST_FRONTEND_TARGET:-}" ]]; then
  exit 1
fi
exec "$ZVMS4_TEST_REAL_MV" "$@"
EOF
chmod +x "$TEST_ROOT/bin/mv"
export ZVMS4_TEST_REAL_MV="$REAL_MV"
export ZVMS4_TEST_FRONTEND_STAGING="$ZVMS4_DEPLOY_ROOT/.github-actions/frontend-$REV_C-$ID_C.new"
export ZVMS4_TEST_FRONTEND_TARGET="$ZVMS4_DEPLOY_ROOT/frontend-dist"
export ZVMS4_TEST_FAIL_FRONTEND_ACTIVATION=1
export ZVMS4_SKIP_CHOWN=0
if bash "$WRAPPER" deploy frontend "$REV_C" "$ID_C"; then
  echo "Expected partial frontend activation to abort deployment" >&2
  exit 1
fi
assert_file 'old-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"
[[ ! -e "$ZVMS4_DEPLOY_ROOT/.github-actions/frontend-$REV_C-$ID_C.previous" ]]
bash "$WRAPPER" rollback frontend "$REV_C" "$ID_C"

# ---- 制品信任边界回归：校验和不符、体积超限、setuid 剥离 ----
REV_D=dddddddddddddddddddddddddddddddddddddddd
ID_D=run-103-1
REV_E=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
ID_E=run-104-1
REV_F=ffffffffffffffffffffffffffffffffffffffff
ID_F=run-105-1

printf '<html>tampered-frontend</html>\n' > "$TEST_ROOT/source/frontend/index.html"
archive frontend "$REV_D" "$TEST_ROOT/source/frontend"
printf '%064d\n' 0 > "$ZVMS4_DEPLOY_INCOMING/frontend-$REV_D.tar.gz.sha256"
if bash "$WRAPPER" deploy frontend "$REV_D" "$ID_D"; then
  echo "Expected checksum mismatch to abort deployment" >&2
  exit 1
fi
assert_file 'old-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"

archive frontend "$REV_E" "$TEST_ROOT/source/frontend"
if ZVMS4_DEPLOY_MAX_ARCHIVE_BYTES=1 bash "$WRAPPER" deploy frontend "$REV_E" "$ID_E"; then
  echo "Expected oversized archive to abort deployment" >&2
  exit 1
fi
assert_file 'old-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"

printf '<html>setuid-frontend</html>\n' > "$TEST_ROOT/source/frontend/index.html"
printf 'probe\n' > "$TEST_ROOT/source/frontend/setuid-probe"
chmod 4755 "$TEST_ROOT/source/frontend/setuid-probe"
archive frontend "$REV_F" "$TEST_ROOT/source/frontend"
bash "$WRAPPER" deploy frontend "$REV_F" "$ID_F"
assert_file 'setuid-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"
probe_mode="$(stat -c '%a' "$ZVMS4_DEPLOY_ROOT/frontend-dist/setuid-probe")"
(( (8#$probe_mode & 04000) == 0 )) || {
  echo "Assertion failed: setuid bit survived extraction (mode $probe_mode)" >&2
  exit 1
}

# ---- 并发互斥回归：锁被占用时第二个进程必须失败，且不触碰现有产物 ----
REV_G=7777777777777777777777777777777777777777
ID_G=run-106-1
printf '<html>locked-frontend</html>\n' > "$TEST_ROOT/source/frontend/index.html"
archive frontend "$REV_G" "$TEST_ROOT/source/frontend"
lock_file="$ZVMS4_DEPLOY_ROOT/.github-actions/deploy.lock"
flock "$lock_file" sleep 30 &
lock_pid=$!
sleep 0.3
if ZVMS4_DEPLOY_LOCK_TIMEOUT=1 bash "$WRAPPER" deploy frontend "$REV_G" "$ID_G"; then
  echo "Expected concurrent deployment to be rejected while the lock is held" >&2
  kill "$lock_pid" 2>/dev/null || true
  exit 1
fi
kill "$lock_pid" 2>/dev/null || true
wait "$lock_pid" 2>/dev/null || true
assert_file 'setuid-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"
bash "$WRAPPER" deploy frontend "$REV_G" "$ID_G"
assert_file 'locked-frontend' "$ZVMS4_DEPLOY_ROOT/frontend-dist/index.html"

echo "Deployment wrapper tests passed"
