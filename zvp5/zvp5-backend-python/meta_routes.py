"""元信息端点：``/api/`` 首页、``/api/health``、``/api/cert``、``/api/version``。

这四个端点都是维护模式的豁免路径（白名单在 ``app_setup``），也是反代探活的
入口，所以刻意不依赖任何 router。

注册拆成三个函数（而不是一个 ``register_all``）：原 ``main.py`` 里首页与
``/api/health`` 之间夹着 6 条 legacy redirect，路由表的**顺序**是契约，拆分后
仍要按原样交错注册（见 ``main`` 底部的装配段）。
"""

from __future__ import annotations

import logging
import os
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import bindings
from db import backends
from db.oracle.engine import ping as oracle_ping

_logger = logging.getLogger(__name__)

# Neutral value returned when the version cannot be resolved from the database.
# Deliberately not a version literal: the CHANGELOG table is the only source of
# truth for the current version, so there is nothing here that can drift.
VERSION_UNKNOWN = "unknown"


async def current_version() -> str:
    """Return the current version, read from the ``CHANGELOG`` table.

    ``ChangelogRepo.list_all()`` is ordered by ``date`` desc, ``version`` desc,
    so the first entry is the latest release and thus the current version.

    Never raises: an empty table or a failing lookup yields
    :data:`VERSION_UNKNOWN`, keeping ``/api/`` and ``/api/version`` at 200.
    """
    try:
        entries = await backends.get_changelog_repo().list_all()
    except Exception as e:
        _logger.exception("Failed to read version from changelog: %s", e)
        return VERSION_UNKNOWN
    if not entries:
        return VERSION_UNKNOWN
    version = entries[0].get("version")
    return str(version) if version else VERSION_UNKNOWN


# 公钥按本文件定位，不依赖 cwd：systemd 的 WorkingDirectory 与手工启动时不同。
# 本模块与 ``main.py`` 同在后端根目录，路径口径与拆分前一致。
CERT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rsa_public_key.pem"
)

# 缓存本体放在 ``main._cert_pem``（经 bindings 读写）：既有测试用
# ``monkeypatch.setattr(main, "_cert_pem", None)`` 复位缓存，缓存必须挂在那个
# 打桩点上；``python main.py`` 时宿主不在 sys.modules，bindings 会退回本地存储。
_cert_lock = threading.Lock()


def _load_cert() -> str:
    """读公钥并在内存里缓存：证书不轮换，只有失败才不缓存（修好后自愈）。"""
    cached = bindings.get("_cert_pem")
    if cached is not None:
        return cached
    with _cert_lock:
        cached = bindings.get("_cert_pem")
        if cached is None:
            with open(
                bindings.get("CERT_PATH", CERT_PATH), "r", encoding="utf-8"
            ) as handle:
                cached = handle.read()
            bindings.set_value("_cert_pem", cached)
        return cached


def register_index(app: FastAPI) -> None:
    """``GET /api/`` —— 首页/探活兼 API 目录。"""

    @app.get("/api/")
    async def home():
        return {
            "status": "ok",
            "code": 200,
            "data": {
                "message": "Welcome to ZVMS API",
                "version": await current_version(),
                "author": "ZZDev",
                "license": "MIT",
                "source": "https://github.com/xiaobo2010/zvp5.git",
                "apis": {
                    "user": "/api/v1/users",
                    "activity": "/api/v1/activities",
                    "group": "/api/v1/groups",
                    "exports": "/api/exports",
                    "imports": "/api/imports",
                    "logs": "/api/logs",
                },
            },
        }


def register_probes(app: FastAPI) -> None:
    """``GET /api/health`` + ``GET /api/cert``。"""

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint."""
        try:
            # ADB is the only backend: always verify DB connectivity.
            # 探针走 bindings：测试在 ``main.oracle_ping`` 上打桩。
            if not await bindings.get("oracle_ping", oracle_ping)():
                raise RuntimeError("database ping returned false")
            return {"status": "ok", "code": 200, "data": "OK"}
        except Exception:
            _logger.exception("Health check failed")
            return JSONResponse(
                status_code=503,
                content={"status": "error", "code": 503, "data": "Service unavailable"},
            )

    @app.get("/api/cert")
    async def get_cert():
        try:
            data = _load_cert()
        except (OSError, UnicodeDecodeError) as exc:
            _logger.warning("cert public key is unreadable: %s", exc)
            return JSONResponse(
                status_code=503,
                content={"status": "error", "code": 503, "data": "证书暂不可用"},
            )
        return {
            "status": "ok",
            "code": 200,
            "data": data,
        }


def register_version(app: FastAPI) -> None:
    """``GET /api/version`` —— 当前版本号（CHANGELOG 第一行）。"""

    @app.get("/api/version")
    async def get_version():
        return {"status": "ok", "code": 200, "data": await current_version()}
