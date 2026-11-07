"""依赖卫生：``python-jose`` / ``ecdsa`` 必须保持零引用。

两条链路的 JWT 都由 ``PyJWT`` 承担（``util/cert.py``）；``python-jose`` 是历史
遗留，留着等于多一份攻击面与一份需要在 CVE 出来时跟进的依赖。用例同时守住
「代码里没人 import」与「声明里没人再装回来」。
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = {"jose", "ecdsa"}
FORBIDDEN_REQUIREMENTS = {"python-jose", "ecdsa"}
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", "tests"}


def _python_sources():
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_no_module_imports_jose_or_ecdsa() -> None:
    offenders = sorted(
        str(path.relative_to(ROOT))
        for path in _python_sources()
        if _imported_modules(path) & FORBIDDEN_MODULES
    )

    assert offenders == []


def test_requirements_do_not_declare_jose_or_ecdsa() -> None:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    declared = {
        line.split("==")[0].split(">=")[0].split("~=")[0].strip().lower()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }

    assert declared & FORBIDDEN_REQUIREMENTS == set()
