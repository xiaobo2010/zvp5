#!/usr/bin/env python3
"""Render the Nginx security-header template without storing deployment domains."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

# 本文件按 `python config/render_nginx_security_headers.py` 独立运行，
# 后端根不在 sys.path 上，先补再导入共用读取器。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from util.env import getenv  # noqa: E402

API_PLACEHOLDER = "__ZVMS_API_ORIGIN__"
WS_PLACEHOLDER = "__ZVMS_API_WS_ORIGIN__"
STATIC_PLACEHOLDER = "__ZVMS_STATIC_ORIGIN__"


def _origin(value: str, *, schemes: set[str], variable: str) -> str:
    origin = value.strip().rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in schemes
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        expected = " or ".join(sorted(schemes))
        raise ValueError(f"{variable} must be an exact {expected} origin: {value!r}")
    return origin


def render(template: str, api_origin: str, ws_origin: str, static_origin: str) -> str:
    api = _origin(api_origin, schemes={"https"}, variable="ZVMS_API_ORIGIN")
    ws = _origin(ws_origin, schemes={"wss"}, variable="ZVMS_API_WS_ORIGIN")
    static = _origin(static_origin, schemes={"https"}, variable="ZVMS_STATIC_ORIGIN")
    rendered = (
        template.replace(API_PLACEHOLDER, api)
        .replace(WS_PLACEHOLDER, ws)
        .replace(STATIC_PLACEHOLDER, static)
    )
    if any(
        placeholder in rendered
        for placeholder in (API_PLACEHOLDER, WS_PLACEHOLDER, STATIC_PLACEHOLDER)
    ):
        raise ValueError("template still contains unresolved origin placeholders")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).with_name("nginx-security-headers.conf"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rendered = render(
        args.template.read_text(encoding="utf-8"),
        getenv("ZVP5_API_ORIGIN", ""),
        getenv("ZVP5_API_WS_ORIGIN", ""),
        getenv("ZVP5_STATIC_ORIGIN", ""),
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write(rendered.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
