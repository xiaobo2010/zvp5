import re
import traceback as tb
from datetime import datetime, timedelta
from typing import Optional, Any, Literal, Annotated, Union
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typings.log import inject_log, ZVMSLog
from typings.user import User
from util.logify import binding_user_credentials
from util.object_id import (
    compulsory_temporary_token,
    get_current_user,
    validate_object_id,
    string_to_option_object_id,
    optional_current_user,
)
from database import db
from util.cert import rsa_decrypt, check_password, create_token, jwt_private_key as aes_key, hash_password
from util.get_user import get_user, get_cached_user, CurrentUser
from util.log import APILogger
from asyncio import sleep, gather
from cktotp import TOTP
from bson import ObjectId

totp_instance = None

def get_totp(totp, *l, **kw):
    digest = totp.at(*l, **kw).upper()
    ofs = 0
    code = digest[:6]
    while len(code) == 6 and (code.isalpha() or code.isnumeric()):
        ofs += 1
        code = digest[ofs:ofs+6]
    if len(code) != 6:
        code = ''
    return code

api = APIRouter()

_regexp = re.compile(r'^[ -\x7e]{8,14}$')

_regexps = [
    re.compile(r'^(?=.*[a-z])'),
    re.compile(r'^(?=.*[A-Z])'),
    re.compile(r'^(?=.*[0-9])'),
    re.compile(r'^(?=.*[^a-zA-Z0-9])')
]

def pwd_strength(pwd: str) -> int:
    if not _regexp.match(pwd):
        return 0
    return int(
        sum(
            map(
                lambda x: bool(x.match(pwd)),
                _regexps
            )
        )
    )

class BaseLoginRequest(BaseModel):
    loginType: str
    password: str
    verifiedName: Optional[str] = None
    verifiedId: Optional[str] = None
    
    def user_filter(self):
        raise HTTPException(
            status_code=400,
            detail='无效登录方式'
        )
    
    def scope(self):
        return 'access_token'
    
    def msg(self):
        return '登录了平台'
    
    def log_type(self):
        return 'user_login'

class PwdLoginRequest(BaseLoginRequest):
    username: str
    newPassword: Optional[str] = None
    loginType: Literal['password']
    
    def user_filter(self):
        return {
            "id": self.username
        }

class PwdAuthzLoginRequest(BaseLoginRequest):
    user: str
    loginType: Literal['password_authz']
    
    def user_filter(self):
        return {
            "_id": validate_object_id(self.user)
        }
    
    def scope(self):
        return 'temporary_token'
    
    def msg(self):
        return '进行身份验证'
    
    def log_type(self):
        return 'user_auth'

type LoginRequest = Annotated[
    Union[
        PwdLoginRequest,
        PwdAuthzLoginRequest
    ],
    Field(discriminator='loginType')
]

@api.post("/login")
async def v1_platform_login(
    auth: LoginRequest,
    logger=Depends(APILogger),
):
    users = await db.zvms_new.users.find(auth.user_filter()).to_list(None)
    
    if not len(users):
        raise HTTPException(
            status_code=404,
            detail="用户不存在",
        )
    
    if len(users) != 1:
        raise HTTPException(
            status_code=500,
            detail="数据库异常：查询到用户名相同的多个用户",
        )
    
    user = users[0]
    userid = str(user['_id'])
    
    invalidate = False
    
    if auth.verifiedId:
        if userid != auth.verifiedId:
            invalidate = True
    elif auth.verifiedName:
        if user["name"] != auth.verifiedName:
            invalidate = True
    
    if invalidate:
        raise HTTPException(
            status_code=403,
            detail=f'这是{auth.verifiedName or auth.verifiedId}的设备，请勿使用他人设备'
        )

    dur = timedelta(days=15)
    
    is_otp_auth = False
    has_reset_passwd = False
    
    match auth.loginType:
        case 'password' | 'password_authz':
            raw_password = rsa_decrypt(auth.password)
            if not (raw_password == user["id"] if user["password"] is None else check_password(raw_password, user["password"])):
                otp = get_totp(get_totp_instance(), time_step=30)
                if raw_password == f'*{otp}*':
                    is_otp_auth = True
                    dur = timedelta(days=1)
                else:
                    raise HTTPException(
                        status_code=403,
                        detail="密码错误"
                    )
            if pwd_strength(raw_password) < 3:
                if auth.loginType == 'password' and auth.newPassword:
                    new_password = rsa_decrypt(auth.newPassword)
                    if pwd_strength(new_password) < 3:
                        raise HTTPException(status_code=403,detail='请使用强密码')
                    else:
                        await db.zvms_new.users.update_one({
                            "_id": user["_id"]
                        }, {
                            "$set": {
                                "password": hash_password(new_password).decode('utf-8')
                            }
                        })
                        has_reset_passwd = True
                elif auth.loginType == 'password':
                    return {
                        "token": None,
                        "profile": None,
                        "otpauth": False
                    }
                else:
                    raise HTTPException(status_code=403,detail='请使用强密码')
            if 'authz' in auth.loginType:
                dur = timedelta(minutes=5)
        case _:
            raise HTTPException(
                status_code=400,
                detail="无效登录方式"
            )
    
    roles = user["roles"]
    
    sess = ObjectId()
    
    token = create_token(
        userid,
        roles,
        duration = dur,
        scope = auth.scope(),
        sid = str(sess),
    )

    cu = CurrentUser(
        **user
    )
    
    cu.session_id = sess
    
    if has_reset_passwd:
        await logger.log(
            f'用户 {user['name']} 修改了密码',
            level='info',
            type='passwd_change',
            user=cu
        )
    
    await logger.log(
        f'用户 {user['name']} {'使用动态码' if is_otp_auth else ''}{auth.msg()}',
        level='warning' if is_otp_auth else 'info',
        type=auth.log_type(),
        user=cu,
    )
    
    del user["password"]
    user["_id"] = userid
    user["class_id"] = str(user["class_id"])

    return {
        "token": token,
        "profile": user,
        "otpauth": is_otp_auth,
    }


@api.get('/totp')
async def generate_totp(
    user=Depends(get_cached_user)
):
    """
    TOTP push test
    
    also testing permission upd
    """
    if "admin" not in user.roles:
        raise HTTPException(status_code=403, detail="权限不足")
    totp = get_totp_instance()
    return {
        "code": get_totp(totp, time_step=30),
        "exp": totp.expires(time_step=30)
    }

def get_totp_instance():
    global totp_instance
    totp = totp_instance
    if totp is None:
        key = bytes.fromhex(aes_key)
        totp = TOTP(key, digest="sha256", digits=0)
        totp_instance = totp
    return totp

all_request_methods = [
    'GET', 'POST', 'DELETE',
    'PUT', 'PATCH', 'HEAD',
    'TRACE', 'PROPFIND', 'OPTIONS'
]

all_search_props = [
    'summary',
    'request.url',
    'request.body',
    'request.ip'
]

@api.get('/logs')
async def get_logs(
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    types: str = Query(''),
    levels: str = Query('error,warning,info'),
    time_start: int | None = Query(None),
    time_end: int | None = Query(None),
    search_props: str = Query('summary'),
    request_methods: str = Query(''),
    user: str = Query(''),
    xuehai_id: int | None = Query(None),
    session: str = Query(''),
    net_session: str = Query(''),
    token=Depends(get_user)
):
    if not token.require('admin'):
        raise HTTPException(
            status_code=403,
            detail="权限不足"
        )
    
    filters = []
    
    if query:
        p = search_props.split(',')
        e = re.escape(query)
        f = lambda: {
            "$regex": e,
            "$options": "i"
        }
        or_ = [
            {
                x: f()
            }
            for x in p
            if x in all_search_props
        ]
        if not len(or_):
            or_.append({
                'summary': f()
            })
        filters.append({
            "$or": or_
        })
    
    # if levels:
    p = (levels or 'error,warning,info').split(',')
    filters.append({
        "level": {
            "$in": p
        }
    })
    
    if types:
        p = types.split(',')
        filters.append({
             "type": {
                "$in": p
            }
        })
    
    if time_start is not None:
        filters.append({
            "timestamp": {
                "$gt": time_start
            }
        })
    
    if time_end is not None:
        filters.append({
            "timestamp": {
                "$lt": time_end
            }
        })
    
    if request_methods:
        p = request_methods.upper().split(',')
        filters.append({
            "request.method": {
                "$in": [
                    x for x in p
                    if x in all_request_methods
                ]
            }
        })
    
    if user == 'null':
        filters.append({
            "user": None
        })
    elif user:
        filters.append({
            "user": validate_object_id(user)
        })
    
    if session == 'null':
        filters.append({
            "session": None
        })
    elif session:
        filters.append({
            "session": validate_object_id(session)
        })
    
    if net_session == 'null':
        filters.append({
            "net_session": None
        })
    elif net_session:
        filters.append({
            "net_session": validate_object_id(net_session)
        })
    
    if xuehai_id is not None:
        filters.append({
            "xuehai_id": xuehai_id
        })
    
    pipeline = [
        {
            "$match": {
                "$and": filters
            } if len(filters) else {}
        },
        {
            "$sort": {
                "_id": -1
            }
        },
        {
            "$skip": (page - 1) * size
        },
        {
            "$limit": size
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "user",
                "foreignField": "_id",
                "as": "user_obj"
            }
        },
        {
            "$unwind": {
                "path": "$user_obj",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$set": {
                "user_obj": {
                    "$ifNull": [
                        "$user_obj",
                        {
                            "name": "$user"
                        }
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": {
                    "$toString": "$_id"
                },
                "request": "$request",
                "user": {
                    "$convert": {
                        "input": "$user",
                        "to": "string",
                        "onError": "",
                        "onNull": "null"
                    }
                },
                "user_name": {
                    "$ifNull": [
                        "$user_obj.name",
                        "未登录"
                    ]
                },
                "xuehai_id": "$xuehai_id",
                "session": {
                    "$convert": {
                        "input": "$session",
                        "to": "string",
                        "onError": "",
                        "onNull": "null"
                    }
                },
                "net_session": {
                    "$convert": {
                        "input": "$net_session",
                        "to": "string",
                        "onError": "",
                        "onNull": "null"
                    }
                },
                "summary": "$summary",
                "type": "$type",
                "level": "$level",
                "timestamp": "$timestamp"
            }
        }
    ]
    
    count_pipeline = [
        pipeline[0],
        {
            "$count": "total"
        }
    ]
    
    total, result = await gather(
        db.zvms_new.logs.aggregate(count_pipeline).to_list(length=1),
        db.zvms_new.logs.aggregate(pipeline).to_list(None)
    )
    
    return {
        "data": result,
        "total": total[0]["total"] if len(total) else 0
    }


@api.get('/cert')
async def get_cert():
    return FileResponse('./rsa_public_key.pem')
