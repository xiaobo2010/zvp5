from fastapi import Request, Depends
from datetime import datetime
from database import db
from .get_user import CurrentUser, get_user, optional_user, get_authz_user, _get_user_for_error_handler
from typing import Optional
lazy from traceback import format_exception_only as fmt_exc
lazy import sys
from bson import ObjectId

def millis():
    """
    Same format as `new Date().getTime()` in JS
    """
    return int(datetime.now().timestamp() * 1000)

def header(req: Request, header_name: str, default: str | None = None):
    return req.headers.get(header_name, None)

def optional_int(val: Optional[str]):
    return None if val is None or val == '' else int(val)

def optional_id(val: Optional[str]):
    try:
        return None if val is None else ObjectId(val)
    except:
        return None

async def request_detail(req: Request):
    return {
        "method": str(req.method).upper(),
        "url": str(req.url),
        "headers": dict(req.headers),
        "body": "", # TODO
        "ip": header(req, 'X-Forwarded-For', req.client.host),
    }, {
        "xuehai_id": optional_int(header(req, 'Xuehai-Id')),
        "net_session": optional_id(req.state.zvp_session) # optional_id(req.cookies.get('zvp_session', None))
    }

class APILogger:
    _req: dict
    _user: Optional[CurrentUser]
    
    def __init__(
        self,
        req: Request,
        user = None
    ):
        self._req = req
        self._user = user
    
    @staticmethod
    def with_user(
        req: Request,
        user = Depends(get_user)
    ):
        return APILogger(req, user)
    
    @staticmethod
    def with_optional_user(
        req: Request,
        user = Depends(optional_user)
    ):
        return APILogger(req, user)
    
    @staticmethod
    def with_authz_user(
        req: Request,
        user = Depends(get_authz_user)
    ):
        return APILogger(req, user)
    
    @staticmethod
    def for_error_handler(
        req: Request,
        user = Depends(_get_user_for_error_handler)
    ):
        return APILogger(req, user)
    
    async def log(
        self,
        summary: str,
        *,
        level: str,
        type: str,
        user: Optional[CurrentUser] = None,
    ):
        try:
            req, data = await request_detail(self._req)
            log_dict = data | {
                "_id": ObjectId(),
                "request": req,
                "user": (user or self._user)._id if (user or self._user) else None,
                "session": (user or self._user).session_id if (user or self._user) else None,
                "type": type,
                "level": level,
                "summary": summary,
                "timestamp": millis(),
            }
            await db.zvms_new.logs.insert_one(log_dict)
        except BaseException as ex:
            print('Failed to save log: ',*fmt_exc(ex), sep='', end='', file=sys.stderr)
            sys.stderr.flush()
    