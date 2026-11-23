"""主题清单与后端注册表的一致性（离线，不连 ADB）。

`zvp5/themes/manifest.json` 是构建器 / Worker / 前端共用的**主题清单**，
`services/theme_registry.py` 的 :data:`THEMES` 是接口、写路径校验与统计的**后端
事实源**。两者漂移会出两种线上问题：

- 清单里有、注册表没有 → 用户能打开那个应用，但切换会被 ``PUT /themes/me``
  以 400 拒绝，账号偏好永远回落到 canary；
- 注册表里有、清单没有 → ``GET /api/v1/themes`` 给出一个 404 的路径。

所以这个测试把「清单 == 注册表」固化成契约，新增主题时两边必须一起改。

离线：只读仓库文件，不 import 路由、不建数据库。

    cd <repo root>/zvp5/zvp5-backend-python && .venv/bin/python -m pytest tests/test_theme_manifest.py -q
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services import theme_registry

# tests/test_theme_manifest.py -> tests -> zvp5/zvp5-backend-python -> zvp5
MANIFEST_PATH = Path(__file__).resolve().parents[2] / "themes" / "manifest.json"

THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.is_file(), f"missing theme manifest: {MANIFEST_PATH}"
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_manifest_declares_themes(manifest):
    themes = manifest["themes"]
    assert isinstance(themes, list) and themes, "manifest.themes must be a non-empty list"
    for theme in themes:
        for key in ("id", "label", "description", "status", "base"):
            assert isinstance(theme.get(key), str) and theme[key], f"{theme}: {key} required"


def test_manifest_themes_match_registry(manifest):
    """id / label / description / status 逐项一致（顺序也一致，它就是展示顺序）。"""
    from_manifest = [
        {
            "id": theme["id"],
            "label": theme["label"],
            "description": theme["description"],
            "status": theme["status"],
        }
        for theme in manifest["themes"]
    ]
    from_registry = theme_registry.theme_items()
    assert from_manifest == from_registry, (
        "zvp5/themes/manifest.json and services/theme_registry.py drifted; "
        "add/update the theme in both places"
    )
    assert {theme["id"] for theme in from_manifest} == set(theme_registry.THEME_IDS)


def test_manifest_default_matches_registry(manifest):
    assert manifest["default"] == theme_registry.DEFAULT_THEME
    assert manifest["default"] in theme_registry.THEME_IDS


def test_manifest_base_convention(manifest):
    """根主题在 `/`，其余主题在 `${prefix}${id}/`；id/base 都要唯一。"""
    prefix = manifest["prefix"]
    assert prefix == "/themes/"
    assert prefix.startswith("/") and prefix.endswith("/")

    seen_ids: set[str] = set()
    seen_bases: set[str] = set()
    for theme in manifest["themes"]:
        assert THEME_ID_RE.match(theme["id"]), f"invalid theme id: {theme['id']!r}"
        assert theme["id"] not in seen_ids, f"duplicate theme id: {theme['id']}"
        seen_ids.add(theme["id"])

        expected = "/" if theme["id"] == manifest["default"] else f"{prefix}{theme['id']}/"
        assert theme["base"] == expected, (
            f"{theme['id']}: base must be {expected!r} (got {theme['base']!r})"
        )
        assert theme["base"] not in seen_bases, f"duplicate base: {theme['base']}"
        seen_bases.add(theme["base"])


def test_non_root_themes_are_buildable(manifest):
    """非根主题必须声明 source 与 build，否则构建器产不出它的目录。"""
    for theme in manifest["themes"]:
        build = theme.get("build") or {}
        if theme["id"] == manifest["default"]:
            assert build.get("kind") == "root", f"{theme['id']}: default theme must be build.kind root"
            continue
        assert build.get("kind") == "vite", f"{theme['id']}: build.kind must be vite"
        source = theme.get("source") or {}
        assert source.get("kind") in {"git", "local"}, f"{theme['id']}: source.kind required"
        assert source.get("path"), f"{theme['id']}: source.path required"
        if source["kind"] == "git":
            assert source.get("ref"), f"{theme['id']}: source.ref required for git sources"
        command = build.get("command")
        assert isinstance(command, list) and command, f"{theme['id']}: build.command required"
