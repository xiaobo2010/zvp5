#!/usr/bin/env bash
# 构建同源多主题的子应用（canary2 / canary-pro），并并入根 canary 产物。
#
# 背景：主题 = 整站多构建。三个主题各自是一份完整前端，不再在同一套页面上
# 换外壳/换样式：
#   canary     → /             （本仓库 v5-canary 的 zvp5/zvp5-frontend，调用方先构建）
#   canary2    → /t/canary2/   （v5-canary2 的 zvms4-modified-main/zvms4-frontend）
#   canary-pro → /t/canary-pro/（v5-canary-pro 的 zvp5/zvp5-frontend-pro）
#
# 本脚本在 CI 里跑在根 canary 构建（pnpm run build）之后、Worker 发布之前：
#   1. 用 worktree 取出另外两个分支的前端源码，各自独立 install + build；
#   2. 把产物拷进根产物 dist/t/<id>/，Worker 直接当静态资源托管；
#   3. 删掉子应用的 Service Worker（避免它的缓存 scope 干扰根应用）；
#   4. 给子应用 index.html 注入 API 基址 meta 与全局主题切换器脚本。
#
# 环境变量（CI 的 preview job 已按根应用注入，未设置时按下面的默认值降级）：
#   VITE_API_BASE          必需，正式 API 基址
#   VITE_API_RACE_BASE     可选，默认同 VITE_API_BASE
#   VITE_API_CONTENT_BASE  可选，默认同 VITE_API_BASE
#
# 用法：bash zvp5/deployment/build-theme-apps.sh   （仓库任意目录执行均可）

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
main_dist="$repo_root/zvp5/zvp5-frontend/dist"
theme_switcher="$main_dist/theme-switcher.js"

: "${VITE_API_BASE:?VITE_API_BASE is required}"
api_base="${VITE_API_BASE%/}"
api_race_base="${VITE_API_RACE_BASE:-$VITE_API_BASE}"
api_content_base="${VITE_API_CONTENT_BASE:-$VITE_API_BASE}"

if [[ ! -f "$main_dist/index.html" ]]; then
  echo "missing $main_dist/index.html: build the root canary frontend first" >&2
  exit 1
fi
if [[ ! -f "$theme_switcher" ]]; then
  echo "missing $theme_switcher: root build must copy public/theme-switcher.js" >&2
  exit 1
fi
command -v pnpm >/dev/null 2>&1 || {
  echo "pnpm is required (CI sets it up via pnpm/action-setup)" >&2
  exit 1
}

workdir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/zvms-theme-apps.XXXXXX")"

cleanup() {
  local tree
  for tree in "$workdir/canary2" "$workdir/canary-pro"; do
    if [[ -d "$tree" ]]; then
      git -C "$repo_root" worktree remove --force "$tree" >/dev/null 2>&1 || true
    fi
  done
  rm -rf "$workdir"
}
trap cleanup EXIT

echo "::group::fetch theme branches"
git -C "$repo_root" fetch origin v5-canary2 v5-canary-pro --depth=1
for tree in canary2 canary-pro; do
  git -C "$repo_root" worktree add --detach "$workdir/$tree" "origin/v5-$tree"
done
echo "::endgroup::"

# 子应用共用的收尾：删 Service Worker + 注入 API 基址与主题切换器（幂等）。
finalize_app() {
  local theme_id="$1"
  local target="$main_dist/t/$theme_id"

  if [[ ! -f "$target/index.html" ]]; then
    echo "build produced no $target/index.html" >&2
    exit 1
  fi

  shopt -s nullglob
  rm -f "$target"/sw.js "$target"/sw.js.map "$target"/workbox-*.js "$target"/workbox-*.js.map
  shopt -u nullglob

  ZVMS_API_BASE="$api_base" python3 - "$target/index.html" <<'PY'
import os
import sys

path = sys.argv[1]
api_base = os.environ["ZVMS_API_BASE"]
meta = '<meta name="zvms-api-base" content="%s">' % api_base
script = '<script src="/theme-switcher.js" defer></script>'

with open(path, encoding="utf-8") as handle:
    html = handle.read()

if 'name="zvms-api-base"' not in html:
    if "</head>" not in html:
        raise SystemExit("no </head> in %s" % path)
    html = html.replace("</head>", "  %s\n  </head>" % meta, 1)
if "theme-switcher.js" not in html:
    if "</body>" not in html:
        raise SystemExit("no </body> in %s" % path)
    html = html.replace("</body>", "  %s\n  </body>" % script, 1)

with open(path, "w", encoding="utf-8") as handle:
    handle.write(html)

assert 'name="zvms-api-base"' in html
assert "theme-switcher.js" in html
PY
}

echo "::group::build canary2"
canary2_src="$workdir/canary2/zvms4-modified-main/zvms4-frontend"
(
  cd "$canary2_src"
  pnpm install --frozen-lockfile
  # 直接用 vite build：主题子应用不需要类型检查/PWA 重新生成，
  # base 决定它挂在根域名下的 /t/canary2/。
  VITE_API_BASE="$api_base" \
  VITE_API_RACE_BASE="$api_race_base" \
  VITE_API_CONTENT_BASE="$api_content_base" \
    pnpm exec vite build --base=/t/canary2/
)
rm -rf "${main_dist:?}/t/canary2"
mkdir -p "$main_dist/t"
cp -a "$canary2_src/dist" "$main_dist/t/canary2"
finalize_app canary2
echo "::endgroup::"

echo "::group::build canary-pro"
pro_src="$workdir/canary-pro/zvp5/zvp5-frontend-pro"
(
  cd "$pro_src"
  # Pro 应用的 router 写死 createWebHistory()：挂在子路径下必须带上 BASE_URL，
  # 否则 /t/canary-pro/ 之外的深链接会解析成根应用的路由。
  sed -i 's/createWebHistory()/createWebHistory(import.meta.env.BASE_URL)/' src/router/index.ts
  grep -q 'createWebHistory(import.meta.env.BASE_URL)' src/router/index.ts
  pnpm install --frozen-lockfile
  VITE_API_BASE="$api_base" pnpm exec vite build --base=/t/canary-pro/
)
rm -rf "${main_dist:?}/t/canary-pro"
cp -a "$pro_src/dist" "$main_dist/t/canary-pro"
finalize_app canary-pro
echo "::endgroup::"

echo "theme apps ready:"
du -sh "$main_dist/t/canary2" "$main_dist/t/canary-pro"
