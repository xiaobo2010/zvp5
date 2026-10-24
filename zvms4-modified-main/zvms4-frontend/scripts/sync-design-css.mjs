#!/usr/bin/env node
/**
 * 把仓库根目录「前端约定/css」同步到 src/assets/styles/yg/。
 *
 * 背景：《前端约定/前端设计准则.md》是义工平台前端视觉的唯一事实来源，
 * 标准样式库（css/）放在仓库根目录，不在 Vite 的 root 之内。直接 import
 * 会踩 server.fs.allow 与构建路径问题，因此采用「构建前同步」方案：
 * 把源目录原样复制到 src/assets/styles/yg/，再由 src/main.ts 引入。
 *
 * 为什么不用 Vite alias：
 *   alias 只能解决「能不能 import」，解决不了「谁是源、谁是副本」。
 *   两边各自可改，样式库必然漂移。同步脚本保证下游目录是纯生成物，
 *   文件头写明来源与禁止手改，改动一律回到源文件。
 *
 * 用法：
 *   npm run design:sync      # 手动同步
 *   npm run dev / npm run build 会自动触发（predev / prebuild）
 *
 * 环境变量：
 *   DESIGN_CSS_SRC  可选，覆盖源目录（默认 <仓库根>/前端约定/css）
 */

import { promises as fs } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
// zvms4-frontend/
const projectRoot = path.resolve(scriptDir, '..')
// 仓库根目录/（前端约定/ 与 zvms4-modified-main/ 同级）
const repoRoot = path.resolve(projectRoot, '..', '..')
const sourceRoot = process.env.DESIGN_CSS_SRC
  ? path.resolve(process.env.DESIGN_CSS_SRC)
  : path.join(repoRoot, '前端约定', 'css')
const targetRoot = path.join(projectRoot, 'src', 'assets', 'styles', 'yg')

/** 生成文件头部的来源声明，写在每个同步产物最前面。 */
function banner(relativePath) {
  const sourceRelative = path.posix.join('前端约定/css', relativePath.split(path.sep).join('/'))
  return [
    '/* ==========================================================================',
    '   本文件由 `npm run design:sync` 自动生成，请勿手工修改。',
    `   来源：${sourceRelative}`,
    '   需要改动请编辑上述源文件后重新执行同步命令；',
    '   直接改本文件会在下次同步时被覆盖，造成样式库与准则漂移。',
    '   ========================================================================== */',
    ''
  ].join('\n')
}

/** 递归收集源目录下的全部 css 文件（相对路径 → 绝对路径）。 */
async function collectCss(dir, base = dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      files.push(...(await collectCss(full, base)))
    } else if (entry.isFile() && entry.name.endsWith('.css')) {
      files.push(path.relative(base, full))
    }
  }
  return files.sort()
}

export async function syncDesignCss() {
  const stat = await fs.stat(sourceRoot).catch(() => null)
  if (!stat || !stat.isDirectory()) {
    throw new Error(
      `找不到标准样式库源目录：${sourceRoot}\n` +
        '请确认仓库根目录下存在「前端约定/css」，或用环境变量 DESIGN_CSS_SRC 指定。'
    )
  }

  const relatives = await collectCss(sourceRoot)
  if (relatives.length === 0) {
    throw new Error(`标准样式库源目录为空：${sourceRoot}`)
  }

  await fs.mkdir(targetRoot, { recursive: true })

  let changed = 0
  for (const relative of relatives) {
    const from = path.join(sourceRoot, relative)
    const to = path.join(targetRoot, relative)
    const content = banner(relative) + (await fs.readFile(from, 'utf8'))
    await fs.mkdir(path.dirname(to), { recursive: true })
    const existing = await fs.readFile(to, 'utf8').catch(() => null)
    if (existing === content) {
      continue
    }
    await fs.writeFile(to, content, 'utf8')
    changed += 1
  }

  // 清掉源目录已删除、同步目录残留的旧产物，避免「僵尸样式」继续生效
  const stale = []
  const walk = async (dir, base = targetRoot) => {
    const entries = await fs.readdir(dir, { withFileTypes: true }).catch(() => [])
    for (const entry of entries) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        await walk(full, base)
      } else if (entry.name.endsWith('.css')) {
        const relative = path.relative(base, full)
        if (!relatives.includes(relative)) {
          stale.push(full)
        }
      }
    }
  }
  await walk(targetRoot)
  for (const file of stale) {
    await fs.rm(file)
  }

  return { copied: relatives.length, changed, removed: stale.length, targetRoot }
}

// 作为脚本直接执行时同步一次；被 import 时不自动执行
const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (invokedDirectly) {
  syncDesignCss()
    .then(({ copied, changed, removed, targetRoot: target }) => {
      console.log(
        `[design:sync] 标准样式库已同步：${copied} 个文件（更新 ${changed}，清理残留 ${removed}）→ ${path.relative(projectRoot, target)}`
      )
    })
    .catch((error) => {
      console.error(`[design:sync] 同步失败：${error.message}`)
      process.exitCode = 1
    })
}
