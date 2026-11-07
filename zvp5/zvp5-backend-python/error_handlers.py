"""异常处理器：422 中文本地化、``InvalidOid`` → 400、兜底 500。

响应体形状/状态码/文案都是前端契约，逐字从 ``main.py`` 搬过来，不顺手改措辞。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from db.ids import InvalidOid

_logger = logging.getLogger(__name__)

# Pydantic 的校验消息是英文（v2 形如 "Input should be a valid integer, unable to
# parse string as an integer"），直接拼进 detail 会把英文糊到用户脸上。这里按前缀
# 归一到中文；上下文（字段路径、具体阈值）由调用处保留，未命中的消息原样输出，
# 保证排查信息不丢。
_VALIDATION_MSG_ZH: tuple[tuple[str, str], ...] = (
    ("Field required", "缺少必填项"),
    ("Input should be a valid integer", "应为整数"),
    ("Input should be a valid string", "应为字符串"),
    ("Input should be a valid boolean", "应为布尔值"),
    ("Input should be a valid list", "应为列表"),
    ("Input should be a valid dict", "应为对象"),
    ("Input should be a valid number", "应为数字"),
    ("Input should be a valid datetime", "应为合法的时间格式"),
    ("Input should be a valid date", "应为合法的日期格式"),
    ("Input should be greater than or equal to", "不能小于允许的最小值"),
    ("Input should be less than or equal to", "不能大于允许的最大值"),
    ("Input should be greater than", "必须大于下限"),
    ("Input should be less than", "必须小于上限"),
    ("String should have at least", "长度短于允许的最小值"),
    ("String should have at most", "长度超过允许的最大值"),
    ("List should have at least", "条数少于允许的最小值"),
    ("List should have at most", "条数超过允许的最大值"),
    ("Extra inputs are not permitted", "包含不支持的字段"),
    ("Input should be", "取值不在允许范围内"),
)


def _localize_validation_message(message: str) -> str:
    for prefix, localized in _VALIDATION_MSG_ZH:
        if message.startswith(prefix):
            return localized
    return message


async def validation_exception_handler(_: Request, exc: RequestValidationError):
    """把 Pydantic 的校验错误翻成中文整句，供前端直接显示。"""
    errors = exc.errors()
    formatted_errors = []

    for error in errors:
        field = " → ".join(map(str, error["loc"]))  # Format location as "body → field"
        message = _localize_validation_message(str(error["msg"]))
        formatted_errors.append(f"{field}: {message}")

    if not formatted_errors:
        return JSONResponse(content={"detail": "请求参数校验失败"}, status_code=422)

    # 逐条拼成多行：字段路径保留原样，用户能定位到具体哪个参数不对。
    error_string = "请求参数校验失败：\n" + "\n".join(formatted_errors)
    return JSONResponse(content={"detail": error_string}, status_code=422)


async def invalid_oid_exception_handler(_: Request, exc: InvalidOid):
    """Map a malformed id caught at the data boundary to HTTP 400.

    Repositories normalize ids via ``db.ids`` at the data boundary; without
    this handler an ``InvalidOid`` escaping a route (e.g. a business id where
    an object id was expected) would fall through to the generic 500 handler.
    """
    return JSONResponse(content={"detail": "无效的对象 ID"}, status_code=400)


async def generic_exception_handler(_: Request, exc: Exception):
    """Catch-all exception handler to return a generic error message."""
    _logger.exception("Unhandled request exception", exc_info=exc)
    return JSONResponse(
        content={"detail": "服务器内部错误，请稍后重试"}, status_code=500
    )


def register(app: FastAPI) -> None:
    """挂到 app 上。注册顺序不参与路由表，但保持与原文件一致的先后。"""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(InvalidOid, invalid_oid_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
