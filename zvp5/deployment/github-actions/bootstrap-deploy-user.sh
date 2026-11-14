#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_USER="${DEPLOY_USER:-github-deploy}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER_SOURCE="${1:-$SCRIPT_DIR/zvp5-deploy}"
PUBLIC_KEY_FILE="${2:-}"
SUDOERS_FILE="/etc/sudoers.d/zvp5-github-actions"
# 旧文件名：改名后清掉，避免两套策略并存。
LEGACY_SUDOERS_FILE="/etc/sudoers.d/zvms4-github-actions"

[[ $EUID -eq 0 ]] || {
  echo "This bootstrap must run as root on the target server." >&2
  exit 1
}
[[ "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
  echo "Invalid DEPLOY_USER: $DEPLOY_USER" >&2
  exit 1
}
[[ -f "$WRAPPER_SOURCE" ]] || {
  echo "Wrapper not found: $WRAPPER_SOURCE" >&2
  exit 1
}

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi

install -m 0755 -o root -g root "$WRAPPER_SOURCE" /usr/local/sbin/zvp5-deploy
# 过渡期保留旧名字：CI 里还没换过来的调用（以及运维手册里的老写法）继续可用。
# sudo 会先把符号链接解析成真实路径再匹配策略，所以下面是按 zvp5-deploy 授的权。
ln -sfn /usr/local/sbin/zvp5-deploy /usr/local/sbin/zvms4-deploy
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/incoming"
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"

if [[ -n "$PUBLIC_KEY_FILE" ]]; then
  [[ -f "$PUBLIC_KEY_FILE" ]] || {
    echo "Public key not found: $PUBLIC_KEY_FILE" >&2
    exit 1
  }
  install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
    "$PUBLIC_KEY_FILE" "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

tmp_sudoers="$(mktemp)"
trap 'rm -f "$tmp_sudoers"' EXIT
cat > "$tmp_sudoers" <<EOF
Defaults:$DEPLOY_USER !requiretty
Defaults:$DEPLOY_USER env_reset
Defaults:$DEPLOY_USER secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/local/sbin/zvp5-deploy deploy backend *
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/local/sbin/zvp5-deploy deploy frontend *
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/local/sbin/zvp5-deploy rollback backend *
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/local/sbin/zvp5-deploy rollback frontend *
EOF
visudo -cf "$tmp_sudoers"
install -m 0440 -o root -g root "$tmp_sudoers" "$SUDOERS_FILE"

if [[ -f "$LEGACY_SUDOERS_FILE" ]]; then
  rm -f "$LEGACY_SUDOERS_FILE"
  echo "Removed legacy sudo policy ${LEGACY_SUDOERS_FILE}."
fi

echo "Installed zvp5-deploy (and the zvms4-deploy symlink) and sudo policy for $DEPLOY_USER."
echo "Add the matching public key to /home/$DEPLOY_USER/.ssh/authorized_keys if it was not supplied."
