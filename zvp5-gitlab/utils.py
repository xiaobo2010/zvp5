from collections.abc import Callable
from typing import *
from types import ModuleType
import datetime as dt
import bson
import fastapi
from fastapi import Depends, Request, Header, Cookie
from time import time as current_time
from base64 import b32encode
from hashlib import sha1
import hmac
from sqlalchemy import Query
from . import config
from pydantic import (
    field_validator,
    field_serializer,
    PlainValidator,
    PlainSerializer,
    WithJsonSchema,
)
from pydantic_core.core_schema import ValidationInfo, SerializationInfo

import enum
from functools import reduce
import operator

def object_id(oid: Optional[str] = None) -> str:
    if oid is None:
        return str(bson.ObjectId())
    try:
        return str(bson.ObjectId(oid))
    except Exception as ex:
        raise fastapi.HTTPException(status_code=400, detail='Object ID 无效') from ex

tz = dt.timezone(dt.timedelta(hours=8))

def timestamp(delta: Optional[dt.timedelta] = None) -> int:
    time = dt.datetime(tz) + delta
    return int(time.timestamp())

    @export
    def format_ts(ts: int) -> str:
        time = dt.fromtimestamp(float(ts), tz)
        return time.isoformat()



async def _get_request_metadata(request: Request, _clck: Optional[str] = Cookie(None), x_platform_version: Optional[int] = Header(None), x_xuehai_id: Optional[int] = Header(None), x_forwarded_for: Optional[str] = Header(None)):
    return {
        'url': request.url.geturl(),
        'ip': None, # TODO: get IP
        'app_version': x_platform_version,
        'clarity_id': _clck.split('%')[0],
        'xuehai_id': x_xuehai_id,
    }

async def get_request_metadata(*, _meta=Depends (_get_request_metadata)):
    return _meta

def generate_totp() -> str:
    '''Used totp algorithm in https://github.com/7086cmd/totp-console'''
    counter = timestamp() // config.TOTP_EXPIRE_SECS
    counter_bytes = counter.to_bytes(8, 'big', signed=False)

    # improvement: original Rust code does extra computation every time
    # if the key specified is not exactly 64 bytes long, but here we overwrite
    # config.TOTP_KEY to avoid unnecessary work.
    if len(config.TOTP_KEY) > 64:
        config.TOTP_KEY = sha1(config.TOTP_KEY).digest().ljust(64, b'\x00')
    elif len(config.TOTP_KEY) < 64:
        config.TOTP_KEY = config.TOTP_KEY.ljust(64, b'\x00')

    # original values of IPAD_BYTE and OPAD_BYTE are 0x36 and 0x5C
    ipad = bytes(x ^ config.TOTP_IPAD_BYTE for x in config.TOTP_KEY)
    opad = bytes(x ^ config.TOTP_OPAD_BYTE for x in config.TOTP_KEY)

    sha = sha1(); sha.update(ipad); sha.update(counter_bytes)
    digest = sha.digest()
    sha = sha1(); sha.update(opad); sha.update(digest)
    digest = sha.digest()

    # original value of OFFSET_BYTE is 19
    ofs = digest[config.OFFSET_BYTE] & 0xF
    code = int.from_bytes(digest[ofs:ofs + 3], 'big', signed=False) & 0x7FFFFFFF

    return str(code)[-config.TOTP_DIGITS:].rjust(config.TOTP_DIGITS, '0')


def paginate(q: Query, page: int, size: int) -> Query:
    return q.limit(size).offset((page - 1) * size)

def escape_str(s: str) -> str:
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')\
        .replace('[', '\\[').replace(']', '\\]')
        
def _coerce_ienum(flag: bool, /, **field_types):
    @field_validator(*field_types)
    def validate(cls, value, _info: ValidationInfo):
        if _info.field_name not in field_types:
            return value
        enumtype = field_types[_info.field_name]
        if _info.mode == 'json':
            if flag and issubclass(enumtype, enum.IntFlag) \
                and isinstance(value, list):
                return reduce(
                    operators.or,
                    map(enumtype.__getitem__, map(str.upper, value))
                )
            return enumtype[value.upper()]
        else:
            if isinstance(value, enumtype):
                return value
            return enumtype(value)
    return validate

coerce_ienum = partial(_coerce_ienum, False)
coerce_iflag = partial(_coerce_ienum, True)

def _serialize_ienum(flag: bool, /, **field_types):
    @field_serializer(*field_types)
    def serialize(cls, value, _info: SerializationInfo):
        if _info.mode == 'json':
            if flag and isinstance(value, enum.IntFlag):
                return [x.name.lower() for x in value]
            return x.name.lower()
        return value
    return serialize

serialize_ienum = partial(_serialize_ienum, False)
serialize_iflag = partial(_serialize_ienum, True)

