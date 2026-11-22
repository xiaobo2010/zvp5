# 接入新主题

每个主题是一套独立构建的完整前端，包含自己的页面、导航和样式。默认主题 `canary` 使用根路径 `/`，新增主题由预览流水线统一发布到 `/themes/<id>/`。接入时需要准备前端源码、添加构建清单条目，并同步后端注册表。

## 1. 准备前端源码

- 源码在同一个仓库的远端分支时，使用 `source.kind: "git"`。`ref` 填分支名，`path` 填前端项目相对该分支根目录的路径。
- 源码随当前分支提交时，使用 `source.kind: "local"`。`path` 填前端项目相对仓库根目录的路径，省略 `ref`。
- 前端构建须生成 `dist/index.html`，静态资源和 History 路由都要支持 Vite 的子路径。使用 Vue Router 时可用 `createWebHistory(import.meta.env.BASE_URL)`。对于暂时无法修改源码的既有分支，清单的 `build.patch` 支持按 `file`、`from`、`to` 声明构建前替换。现有的 Pro 主题提供了一个例子。[主题清单](./manifest.json)

## 2. 在清单中登记

将以下对象加入 `manifest.json` 的 `themes` 数组，并替换示例分支、目录和文案。`id` 最长 32 个字符，以小写字母或数字开头，其余字符可以是小写字母、数字或连字符。`id` 和 `base` 在清单中都须唯一，`base` 固定为 `/themes/<id>/`。[主题清单](./manifest.json)

```json
{
  "id": "canary-example",
  "label": "Canary Example",
  "description": "用于预览的新前端",
  "status": "experimental",
  "base": "/themes/canary-example/",
  "source": {
    "kind": "git",
    "ref": "v5-canary-example",
    "path": "zvp5/zvp5-frontend-example"
  },
  "build": {
    "kind": "vite",
    "install": ["pnpm", "install", "--frozen-lockfile"],
    "command": ["pnpm", "exec", "vite", "build"]
  }
}
```

预览构建器会给 `build.command` 追加 `--base=/themes/canary-example/`，将子应用产物并入主站产物，注入主题切换器，并生成供浏览器读取的 `/themes.json`。所有主题复用同一套构建流程和 Worker 路由。[构建器](../deployment/build-theme-apps.mjs)

## 3. 同步后端注册表

在 `services/theme_registry.py` 的 `THEMES` 元组加入对应的 `Theme(...)`。`id`、`label`、`description`、`status` 和条目顺序须与清单一致。注册表负责校验账号的默认主题，并提供管理员统计所需的主题目录。已有 `USERS.THEME` 字段可容纳新主题 ID。[后端主题注册表](../zvp5-backend-python/services/theme_registry.py)

## 4. 校验并发布预览

从仓库根目录执行轻量校验：

```bash
node zvp5/deployment/build-theme-apps.mjs --validate
(cd zvp5/zvp5-backend-python && .venv/bin/python -m pytest -q tests/test_theme_manifest.py)
```

把清单和注册表改动一起推送到 `v5-canary`。预览流水线会构建并发布完整子应用。发布后检查 `/themes.json` 中的新条目，打开 `/themes/<id>/` 和一条实际的页面深链接，确认两者都显示目标应用。最后从主站个人资料页切入，并在子应用的「主题」菜单中切回。[预览发布说明](../deployment/preview-worker/README.md)

账号默认主题和管理台统计由预览站所连接的后端提供。验收这两项时，确认该后端已部署新的注册表，并已应用现有主题功能所需的 V055 迁移。
