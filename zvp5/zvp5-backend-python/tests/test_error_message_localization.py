"""main.py 的全局错误出口必须给中文：422 校验失败、400 非法对象 ID、500 内部错误。

这些 handler 是「用户能看到的最后一道文案」，任何一条回到英文都会直接把
Pydantic / 兜底串糊到界面上，所以单独钉住。
"""

from __future__ import annotations

import asyncio
import json

from fastapi.exceptions import RequestValidationError

import main
from db.ids import InvalidOid


def _body(response) -> dict:
    return json.loads(response.body)


def test_validation_message_known_prefixes_are_localized() -> None:
    assert main._localize_validation_message("Field required") == "缺少必填项"
    assert (
        main._localize_validation_message(
            "Input should be a valid integer, unable to parse string as an integer"
        )
        == "应为整数"
    )
    assert (
        main._localize_validation_message("Input should be greater than or equal to 1")
        == "不能小于允许的最小值"
    )


def test_validation_message_unknown_text_is_kept_verbatim() -> None:
    assert main._localize_validation_message("some brand new pydantic text") == (
        "some brand new pydantic text"
    )


def test_validation_handler_returns_chinese_with_field_path() -> None:
    exc = RequestValidationError(
        [
            {
                "loc": ("body", "page"),
                "msg": "Input should be a valid integer, unable to parse string as an integer",
                "type": "int_parsing",
            }
        ]
    )
    response = asyncio.run(main.validation_exception_handler(None, exc))
    assert response.status_code == 422
    detail = _body(response)["detail"]
    assert detail == "请求参数校验失败：\nbody → page: 应为整数"
    assert "Input should be" not in detail


def test_validation_handler_without_entries_still_speaks_chinese() -> None:
    response = asyncio.run(
        main.validation_exception_handler(None, RequestValidationError([]))
    )
    assert response.status_code == 422
    assert _body(response)["detail"] == "请求参数校验失败"


def test_invalid_object_id_handler_is_chinese() -> None:
    response = asyncio.run(
        main.invalid_oid_exception_handler(None, InvalidOid("not-an-oid"))
    )
    assert response.status_code == 400
    assert _body(response)["detail"] == "无效的对象 ID"
