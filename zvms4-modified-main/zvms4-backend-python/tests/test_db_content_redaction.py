"""通用查询控制台的脱敏兜底（列名 + 值形状）离线用例。

替身会话只回答 ``exec_driver_sql``，不建任何真实连接。覆盖四种情况：

1. 别名绕过：``SELECT TOKEN_HASH AS x`` 的返回列名是 ``x``，值仍须脱敏；
2. 列名命中：既有规则（``*_HASH`` / ``PASSWORD`` / ``SECRET``）不被削弱；
3. 24 位 hex 的业务 oid 必须原样返回（脱敏过度等于控制台不可用）；
4. 普通文本/数字不被误伤。

跑法：``.venv/bin/python -m pytest tests/test_db_content_redaction.py -q``
"""

from __future__ import annotations

import pytest

from services import db_content

# 形状逼真的凭据样本（全部是随机占位，不对应任何真实账号）。
BCRYPT = "$2b$12$" + "Q" * 53
ARGON2 = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaHZhbHVl"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI2NWYwYzBmZiJ9.c2lnbmF0dXJl"
SHA256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
MD5 = "5d41402abc4b2a76b9719d911017c592"
OID24 = "65f0c0ffee1234567890abcd"


class _Result:
    def __init__(self, columns: list[str], rows: list[tuple]) -> None:
        self._columns = columns
        self._rows = rows

    def keys(self) -> list[str]:
        return self._columns

    def fetchmany(self, size: int) -> list[tuple]:
        return self._rows[:size]


class _Connection:
    def __init__(self, result: _Result) -> None:
        self._result = result

    async def exec_driver_sql(self, _sql: str) -> _Result:
        return self._result

    async def get_raw_connection(self) -> object:
        raise RuntimeError("替身会话没有驱动连接")


class _Session:
    def __init__(self, result: _Result) -> None:
        self._result = result

    async def connection(self) -> _Connection:
        return _Connection(self._result)


async def _query(columns: list[str], rows: list[tuple]) -> dict:
    return await db_content.run_query(
        _Session(_Result(columns, rows)), "SELECT * FROM t", limit=10
    )


@pytest.mark.asyncio
async def test_alias_cannot_bypass_redaction() -> None:
    """列名是别名时，值形状仍然兜住。"""
    data = await _query(["X", "Y", "Z", "W"], [(BCRYPT, JWT, SHA256, MD5)])

    assert data["columns"] == ["X", "Y", "Z", "W"]
    assert data["rows"] == [{"X": "***", "Y": "***", "Z": "***", "W": "***"}]


@pytest.mark.asyncio
async def test_prefixed_credentials_are_redacted() -> None:
    data = await _query(
        ["A", "B", "C"],
        [("sk-live-abcdef123456", "zvk_0123456789abcdef", "Bearer abc.def.ghi")],
    )

    assert data["rows"] == [{"A": "***", "B": "***", "C": "***"}]


def test_column_name_rule_is_unchanged() -> None:
    assert db_content._redact_cell("PASSWORD_HASH", "plain-text") == "***"
    assert db_content._redact_cell("TOKEN_HASH", "short") == "***"
    assert db_content._redact_cell("api_secret", "short") == "***"
    assert db_content._redact_cell("PAYLOAD", "short") == "***"


@pytest.mark.asyncio
async def test_object_ids_are_not_redacted() -> None:
    """24 位 hex 是活动/成员 oid，误伤会让整个控制台不可用。"""
    data = await _query(["ACTIVITY", "MEMBER"], [(OID24, "65f0c0ffee1234567890abce")])

    assert data["rows"] == [
        {"ACTIVITY": OID24, "MEMBER": "65f0c0ffee1234567890abce"}
    ]


def test_plain_values_are_untouched() -> None:
    assert db_content._redact_cell("NAME", "夏令营义工活动") == "夏令营义工活动"
    assert db_content._redact_cell("BUSINESS_ID", 20261028) == 20261028
    assert db_content._redact_cell("NOTE", "sk 开头的说明文字") == "sk 开头的说明文字"
    assert db_content._redact_cell("NOTE", "Bearer") == "Bearer"
    assert db_content._redact_cell("NOTE", None) is None
