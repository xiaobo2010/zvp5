#!/usr/bin/env node
/**
 * 构建同源多主题的子应用，并并入根 canary 产物。
 *
 * 唯一事实源是 `zvp5/themes/manifest.json`：
 *   - 根主题（`build.kind: "root"`）由本仓库前端自己构建，这里只校验产物存在；
 *   - 其余主题（`build.kind: "vite"`）用 `git worktree` 取出清单里写的分支/目录，
 *     应用可选 patch、执行 install 与 build（自动追加 `--base=<base>`），
 *     再把产物拷进 `zvp5/zvp5-frontend/dist<base>`（即 `dist/themes/<id>/`）。
 *
 * 新增主题只需要在清单里加一条（外加后端 `services/theme_registry.py` 一条），
 * 不要在 Worker / 前端 / CI 里再写主题 id 列表。
 *
 * 环境变量（CI 的 preview job 已按根应用注入）：
 *   VITE_API_BASE          必需，正式 API 基址，会写进子应用 index.html 的 meta
 *   VITE_API_RACE_BASE     可选，未设置或不是绝对 HTTPS URL 时不透传（同源由主题自行推导）
 *   VITE_API_CONTENT_BASE  可选，未设置或不是绝对 HTTPS URL 时不透传（同源由主题自行推导）
 *
 * 用法：
 *   node zvp5/deployment/build-theme-apps.mjs              # 完整构建
 *   node zvp5/deployment/build-theme-apps.mjs --validate    # 只校验清单，不碰 git/产物
 */

import { execFileSync } from 'node:child_process'
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync
} from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(SCRIPT_DIR, '..', '..')
const MANIFEST_PATH = path.join(REPO_ROOT, 'zvp5', 'themes', 'manifest.json')
const MAIN_DIST = path.join(REPO_ROOT, 'zvp5', 'zvp5-frontend', 'dist')

/** 主题 id 的唯一定义：小写字母/数字开头，后续小写字母/数字/连字符。 */
const THEME_ID_RE = /^[a-z0-9][a-z0-9-]*$/
/** 主题引导/清单的缓存版本；改脚本或引导文案时同步递增。 */
const SWITCHER_VERSION = '20261125'

class ThemeBuildError extends Error {}

/** 任何清单/构建错误都抛异常而不是 process.exit，保证 finally 里的 worktree 清理会执行。 */
function fail(message) {
  throw new ThemeBuildError(message)
}

function log(message) {
  console.log(`[themes] ${message}`)
}

/** 清单里的 id 必须能直接当 URL 路径段用，避免 `../` 之类逃出 `/themes/`。 */
function assertThemeId(id, where) {
  if (typeof id !== 'string' || !THEME_ID_RE.test(id)) {
    fail(`${where}: invalid theme id ${JSON.stringify(id)} (expected ${THEME_ID_RE})`)
  }
}

/**
 * 读取并校验清单。约定：
 *   - id 唯一且是合法 slug；
 *   - default 必须指向某个主题；
 *   - base 唯一；根主题 base 为 `/`，其它主题 base 必须等于 `${prefix}${id}/`；
 *   - prefix 以 `/` 开头和结尾。
 */
function loadManifest() {
  if (!existsSync(MANIFEST_PATH)) {
    fail(`missing manifest: ${path.relative(REPO_ROOT, MANIFEST_PATH)}`)
  }
  let manifest
  try {
    manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8'))
  } catch (error) {
    fail(`manifest is not valid JSON: ${error.message}`)
  }

  if (!Array.isArray(manifest.themes) || manifest.themes.length === 0) {
    fail('manifest.themes must be a non-empty array')
  }
  const prefix = manifest.prefix
  if (typeof prefix !== 'string' || !prefix.startsWith('/') || !prefix.endsWith('/')) {
    fail(`manifest.prefix must start and end with "/", got ${JSON.stringify(prefix)}`)
  }
  if (!manifest.themes.some((theme) => theme?.id === manifest.default)) {
    fail(`manifest.default ${JSON.stringify(manifest.default)} is not in themes[]`)
  }

  const seenIds = new Set()
  const seenBases = new Set()
  for (const [index, theme] of manifest.themes.entries()) {
    const where = `themes[${index}]`
    if (!theme || typeof theme !== 'object') {
      fail(`${where} must be an object`)
    }
    assertThemeId(theme.id, where)
    if (seenIds.has(theme.id)) {
      fail(`${where}: duplicate theme id ${JSON.stringify(theme.id)}`)
    }
    seenIds.add(theme.id)

    for (const key of ['label', 'description', 'status', 'base']) {
      if (typeof theme[key] !== 'string' || !theme[key]) {
        fail(`${where} (${theme.id}): ${key} must be a non-empty string`)
      }
    }

    const expectedBase = theme.id === manifest.default ? '/' : `${prefix}${theme.id}/`
    if (theme.base !== expectedBase) {
      fail(
        `${where} (${theme.id}): base must be ${JSON.stringify(expectedBase)} ` +
          `(got ${JSON.stringify(theme.base)}); non-root themes live under ${prefix}<id>/`
      )
    }
    if (theme.id === manifest.default && theme.build?.kind !== 'root') {
      fail(`${where} (${theme.id}): default theme must be build.kind "root"`)
    }
    if (seenBases.has(theme.base)) {
      fail(`${where} (${theme.id}): duplicate base ${JSON.stringify(theme.base)}`)
    }
    seenBases.add(theme.base)

    const kind = theme.build?.kind
    if (kind === 'root') {
      continue
    }
    if (kind !== 'vite') {
      fail(`${where} (${theme.id}): unsupported build.kind ${JSON.stringify(kind)}`)
    }
    const source = theme.source
    if (!source || (source.kind !== 'git' && source.kind !== 'local')) {
      fail(`${where} (${theme.id}): source.kind must be "git" or "local"`)
    }
    if (typeof source.path !== 'string' || !source.path) {
      fail(`${where} (${theme.id}): source.path is required`)
    }
    if (source.kind === 'git' && !/^[A-Za-z0-9._/-]+$/.test(String(source.ref || ''))) {
      fail(`${where} (${theme.id}): source.ref must be a branch name, got ${JSON.stringify(source.ref)}`)
    }
    if (typeof theme.build.command?.[0] !== 'string') {
      fail(`${where} (${theme.id}): build.command must be a non-empty argv array`)
    }
    if (theme.build.install && typeof theme.build.install[0] !== 'string') {
      fail(`${where} (${theme.id}): build.install must be an argv array`)
    }
    for (const patch of theme.build.patch ?? []) {
      if (typeof patch?.file !== 'string' || typeof patch.from !== 'string' || typeof patch.to !== 'string') {
        fail(`${where} (${theme.id}): each patch needs string file/from/to`)
      }
      if (!patch.from) {
        fail(`${where} (${theme.id}): patch.from must not be empty`)
      }
    }
  }

  if (!seenIds.has(manifest.default)) {
    fail(`manifest.default ${JSON.stringify(manifest.default)} is not in themes[]`)
  }

  return manifest
}

function run(command, args, options = {}) {
  log(`$ ${[command, ...args].join(' ')}${options.cwd ? `  (cwd: ${options.cwd})` : ''}`)
  return execFileSync(command, args, { stdio: 'inherit', ...options })
}

function git(args, options = {}) {
  return run('git', ['-C', REPO_ROOT, ...args], options)
}

/** 对 worktree 里的源码应用清单声明的字符串替换（幂等：已经是目标值就跳过）。 */
function applyPatches(sourceDir, theme) {
  for (const patch of theme.build.patch ?? []) {
    const file = path.join(sourceDir, patch.file)
    if (!existsSync(file)) {
      fail(`${theme.id}: patch target ${patch.file} does not exist`)
    }
    const before = readFileSync(file, 'utf8')
    if (before.includes(patch.to)) {
      log(`${theme.id}: ${patch.file} already patched`)
      continue
    }
    if (!before.includes(patch.from)) {
      fail(`${theme.id}: ${patch.file} does not contain ${JSON.stringify(patch.from)}`)
    }
    writeFileSync(file, before.split(patch.from).join(patch.to))
    log(`${theme.id}: patched ${patch.file}`)
  }
}

function escapeAttribute(value) {
  return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

/**
 * 主题产物收尾（幂等）：
 *   - 删 Service Worker（否则它的缓存 scope 会干扰根应用）；
 *   - 把根绝对资源引用 `="/assets/` 改回子应用自己的基路径；
 *   - 注入 API 基址 meta 与全局主题切换器。
 */
function finalizeApp(theme, apiBase) {
  const target = path.join(MAIN_DIST, theme.base.replace(/^\//, ''))
  const indexPath = path.join(target, 'index.html')
  if (!existsSync(indexPath)) {
    fail(`${theme.id}: build produced no ${path.relative(REPO_ROOT, indexPath)}`)
  }

  for (const name of readdirSync(target)) {
    if (theme.base !== '/' && (name === 'sw.js' || name === 'sw.js.map' || name.startsWith('workbox-'))) {
      rmSync(path.join(target, name), { force: true })
    }
  }

  let html = readFileSync(indexPath, 'utf8')
  const assetPrefix = `${theme.base}assets/`
  // Vite 的 externalize-inline-scripts 插件写的是根绝对路径 /assets/boot-*.js；
  // 子应用必须改回自己的基路径，否则会加载根 canary 的同名 boot 脚本。
  if (theme.base !== '/' && html.includes('="/assets/')) {
    html = html.split('="/assets/').join(`="${assetPrefix}`)
  } else if (theme.base !== '/') {
    log(`${theme.id}: no root "/assets/" reference in index.html (nothing to rewrite)`)
  }

  const meta = `<meta name="zvms-api-base" content="${escapeAttribute(apiBase)}">`
  const existingMeta = /<meta\s+name="zvms-api-base"[^>]*>/
  if (existingMeta.test(html)) {
    html = html.replace(existingMeta, meta)
  } else {
    if (!html.includes('</head>')) {
      fail(`${theme.id}: index.html has no </head> to inject the API base meta`)
    }
    html = html.replace('</head>', `  ${meta}\n  </head>`)
  }
  const stylesheet = `<link rel="stylesheet" href="/theme-switcher.css?v=${SWITCHER_VERSION}">`
  if (!html.includes('theme-switcher.css')) {
    html = html.replace('</head>', `  ${stylesheet}\n  </head>`)
  }

  const script = `<script src="/theme-switcher.js?v=${SWITCHER_VERSION}" defer></script>`
  if (!html.includes('theme-switcher.js')) {
    if (!html.includes('</body>')) {
      fail(`${theme.id}: index.html has no </body> to inject the theme switcher`)
    }
    html = html.replace('</body>', `  ${script}\n  </body>`)
  }

  writeFileSync(indexPath, html)

  const written = readFileSync(indexPath, 'utf8')
  if (
    !written.includes('name="zvms-api-base"') ||
    !written.includes('theme-switcher.js') ||
    !written.includes('theme-switcher.css')
  ) {
    fail(`${theme.id}: failed to inject API base meta / theme switcher`)
  }
}

/** 运行清单：只有前端和 Worker 运行时需要的字段，不含 source/build。 */
function writeRuntimeManifest(manifest) {
  const runtime = {
    default: manifest.default,
    prefix: manifest.prefix,
    // Student-only themes are served through the authenticated registry; keep
    // them out of the public static catalog so the unauthenticated fallback
    // cannot advertise a theme the caller may not select.
    themes: manifest.themes
      .filter((theme) => theme.audience !== 'students')
      .map((theme) => ({
        id: theme.id,
        label: theme.label,
        description: theme.description,
        status: theme.status,
        base: theme.base
      }))
  }
  const target = path.join(MAIN_DIST, 'themes.json')
  writeFileSync(target, `${JSON.stringify(runtime, null, 2)}\n`)
  log(`wrote ${path.relative(REPO_ROOT, target)}`)
}

function main() {
  const validateOnly = process.argv.includes('--validate')
  const manifest = loadManifest()
  log(
    `manifest ok: ${manifest.themes.length} theme(s), default=${manifest.default}, ` +
      `prefix=${manifest.prefix}`
  )
  if (validateOnly) {
    return
  }

  for (const required of ['index.html', 'theme-switcher.js', 'theme-switcher.css']) {
    if (!existsSync(path.join(MAIN_DIST, required))) {
      fail(`missing ${path.relative(REPO_ROOT, path.join(MAIN_DIST, required))}: build the root canary frontend first`)
    }
  }

  const apiBaseRaw = process.env.VITE_API_BASE
  if (!apiBaseRaw) {
    fail('VITE_API_BASE is required')
  }
  const apiBase = apiBaseRaw.replace(/\/+$/, '')
  const buildEnv = { ...process.env, VITE_API_BASE: apiBase }

  // 子应用对这两个基址的校验比根应用严：一旦出现在环境里就必须是绝对 HTTPS URL。
  // 生产是同源部署（VITE_API_BASE=/api），把相对基址当默认值塞进去会让主题构建
  // 直接报 "must be an absolute URL"。留空时主题运行时按页面源推导（pageOrigin +
  // /api），同源语义不变；只有真的是绝对地址才透传给子应用。
  for (const name of ['VITE_API_RACE_BASE', 'VITE_API_CONTENT_BASE']) {
    const configured = (process.env[name] || '').trim()
    const candidate = configured || apiBase
    let absolute = false
    if (candidate) {
      try {
        absolute = new URL(candidate).protocol === 'https:'
      } catch {
        absolute = false
      }
    }
    if (absolute) {
      buildEnv[name] = candidate
    } else {
      // 继承来的相对值也必须删掉，否则子应用仍会读到它并报错。
      delete buildEnv[name]
    }
  }

  finalizeApp(manifest.themes.find((theme) => theme.id === manifest.default), apiBase)

  // 清掉上一次构建留下的旧主题目录：清单删除主题后不能继续在线上留一个孤儿应用。
  rmSync(path.join(MAIN_DIST, 'themes'), { recursive: true, force: true })

  const built = manifest.themes.filter((theme) => theme.build.kind === 'vite')
  if (!built.length) {
    log('no sub-app themes to build')
    writeRuntimeManifest(manifest)
    return
  }

  const commandExists = (name) => {
    try {
      // 用位置参数传 name，避免把清单里的命令名拼进 shell 字符串。
      execFileSync('sh', ['-c', 'command -v "$1"', 'sh', name], { stdio: 'ignore' })
      return true
    } catch {
      return false
    }
  }
  for (const theme of built) {
    const tool = theme.build.install?.[0] || theme.build.command[0]
    if (!commandExists(tool)) {
      fail(`${theme.id}: "${tool}" is required but not on PATH`)
    }
  }

  const workRoot = mkdtempSync(path.join(process.env.RUNNER_TEMP || tmpdir(), 'zvms-theme-apps-'))
  const worktrees = []

  try {
    // 同一分支的多个主题只 fetch 一次。
    const fetchedRefs = new Set()
    for (const theme of built) {
      let sourceDir

      if (theme.source.kind === 'local') {
        sourceDir = path.resolve(REPO_ROOT, theme.source.path)
        if (!existsSync(sourceDir)) {
          fail(`${theme.id}: local source ${theme.source.path} does not exist`)
        }
      } else {
        const ref = theme.source.ref
        if (!fetchedRefs.has(ref)) {
          // 浅克隆下 `fetch origin <ref> --depth=1` 只更新 FETCH_HEAD，不建
          // refs/remotes/origin/<ref>；用显式 refspec 才能稳定 worktree add。
          git(['fetch', '--depth=1', 'origin', `+refs/heads/${ref}:refs/remotes/origin/${ref}`])
          fetchedRefs.add(ref)
        }
        const treeDir = path.join(workRoot, theme.id)
        git(['worktree', 'add', '--detach', treeDir, `refs/remotes/origin/${ref}`])
        worktrees.push(treeDir)
        sourceDir = path.join(treeDir, theme.source.path)
        if (!existsSync(sourceDir)) {
          fail(`${theme.id}: ${theme.source.path} does not exist on ${ref}`)
        }
      }

      log(`building ${theme.id} from ${theme.source.path} → ${theme.base}`)

      applyPatches(sourceDir, theme)

      if (theme.build.install?.length) {
        run(theme.build.install[0], theme.build.install.slice(1), {
          cwd: sourceDir,
          env: buildEnv
        })
      }

      // Serve compiled files under the static host's existing CORS-enabled /assets/ location.
      // Keep navigation on the page origin, including when Vite emits absolute CDN URLs.
      const staticBase = buildEnv.VITE_STATIC_BASE || '/'
      const split = staticBase !== '/'
      const staticDir = `assets/themes/${theme.id}/`
      const assetBase = split ? new URL(staticDir, staticBase).href : theme.base
      run(theme.build.command[0], [...theme.build.command.slice(1), `--base=${assetBase}`], {
        cwd: sourceDir,
        env: { ...buildEnv, VITE_THEME_PAGE_BASE: theme.base }
      })

      const builtDist = path.join(sourceDir, 'dist')
      if (!existsSync(path.join(builtDist, 'index.html'))) {
        fail(`${theme.id}: build produced no dist/index.html`)
      }
      const target = path.join(MAIN_DIST, theme.base.replace(/^\//, ''))
      rmSync(target, { recursive: true, force: true })
      mkdirSync(path.dirname(target), { recursive: true })
      cpSync(builtDist, target, { recursive: true })
      finalizeApp(theme, apiBase)
      if (split) {
        const delivery = path.join(MAIN_DIST, staticDir)
        mkdirSync(delivery, { recursive: true })
        for (const name of readdirSync(target)) {
          if (name !== 'index.html') renameSync(path.join(target, name), path.join(delivery, name))
        }
      }
      log(`${theme.id} ready at ${path.relative(REPO_ROOT, target)}`)
    }

    writeRuntimeManifest(manifest)
    log('theme apps ready: ' + built.map((theme) => theme.base).join(', '))
  } finally {
    for (const tree of worktrees) {
      try {
        git(['worktree', 'remove', '--force', tree], { stdio: 'ignore' })
      } catch {
        // worktree 已经被移除或分支被删：继续清理临时目录即可
      }
    }
    if (worktrees.length) {
      try {
        git(['worktree', 'prune'], { stdio: 'ignore' })
      } catch {
        // prune 失败不影响构建结果
      }
    }
    rmSync(workRoot, { recursive: true, force: true })
  }
}

try {
  main()
} catch (error) {
  const message = error instanceof ThemeBuildError ? error.message : error?.message || String(error)
  console.error(`[themes] ${message}`)
  process.exitCode = 1
}
