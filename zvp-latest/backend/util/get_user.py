from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from .object_id import validate_object_id
from database import db
from types import SimpleNamespace
from typing import Optional, Coroutine, Any
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime
import jwt
from .cert import jwt_decode

usercache = {}

class TokenExpired(Exception):
    pass

class ScopeMismatch(Exception):
    pass

class CurrentUser(SimpleNamespace):
    """
    User info
    
    Not using Pydantic models because we do not need
    validation here.
    """
    _id: ObjectId
    name: str
    roles: list[str]
    class_id: ObjectId
    session_id: ObjectId
    
    def require(
        self,
        *permissions: str
    ) -> bool:
        return any(
            x in self.roles
            for x in permissions
        )

type _Async[T] = Coroutine[T, Any, Any]

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl='/api/v1/platform/login',
    auto_error=False
)

async def get_user_by_id(oid: str, code: int = 404, cache: bool = False):
    if cache:
        result = usercache.get(oid, None)
    else:
        result = None
    
    if not result:
        result = await db.zvms_new.users.find_one({
            "_id": validate_object_id(oid)
        })
        
        if cache:
            usercache[oid] = result
    
    if not result:
        raise HTTPException(
            status_code=code,
            detail='用户不存在'
        )
    
    return CurrentUser(**result)

async def _generic_get_user(
    token: Optional[str],
    scope: str = 'access_token',
    action: str = '登录',
    strict: bool = False,
    do_cache: bool = False
) -> _Asnyc[Optional[CurrentUser]]:
    if not token:
        if strict:
            raise HTTPException(
                status_code=401,
                detail=f'未{action}'
            )
        else:
            return None
    
    try:
        payload = jwt_decode(token)
        
        payload['sub']
        
        if scope and payload['scope'] != scope:
            raise ScopeMismatch()
        
        if datetime.utcnow() >= datetime.fromtimestamp(payload['exp']):
            raise TokenExpired()
        
        user = await get_user_by_id(
            payload['sub'], 401, do_cache
        )
        
        user.session_id = ObjectId(payload['sid']) if 'sid' in payload else ObjectId(payload['jti']) if 'jti' in payload else None
        
        return user
    except jwt.ExpiredSignatureError, TokenExpired:
        raise HTTPException(
            status_code=401,
            detail=f'{action}已过期'
        )
    except jwt.PyJWTError, KeyError, ScopeMismatch, InvalidId:
        raise HTTPException(
            status_code=401,
            detail='无效令牌'
        )

async def get_user(
    token: Optional[str] = Depends(oauth2_scheme)
) -> _Async[CurrentUser]:
    return await _generic_get_user(
        token, strict=True
    )

async def get_cached_user(
    token: Optional[str] = Depends(oauth2_scheme)
) -> _Async[CurrentUser]:
    return await _generic_get_user(
        token, strict=True, do_cache=True
    )

async def optional_user(
    token: Optional[str] = Depends(oauth2_scheme)
) -> _Async[Optional[CurrentUser]]:
    return await _generic_get_user(
        token, strict=False
    )

async def get_authz_user(
    token: Optional[str] = Depends(oauth2_scheme)
) -> _Async[CurrentUser]:
    return await _generic_get_user(
        token, scope='temporary_token',
        action='授权', strict=True
    )

async def _get_user_for_error_handler(
    token: Optional[str] = Depends(oauth2_scheme)
) -> _Async[Optional[CurrentUser]]:
    try:
        return await _generic_get_user(
            token, scope='', strict=False
        )
    except BaseException:
        return None
