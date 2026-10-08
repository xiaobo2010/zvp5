import re
import traceback as tb
from datetime import datetime, timedelta
from typing import Optional, Any, Literal
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
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
from util.cert import rsa_decrypt, check_password, create_token, jwt_private_key as aes_key
from util.get_user import get_user, get_cached_user, CurrentUser
from util.log import APILogger
from asyncio import sleep, gather
from bson import ObjectId
from .platform import get_totp, get_totp_instance

api = APIRouter()

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

@api.put("/{user_id}/password")
async def change_user_password(
    user_id: str,
    payload: PasswordChangeRequest,
    logger=Depends(APILogger),
):

    users = await db.zvms_new.users.find({
        "_id": validate_object_id(user_id)
    }).to_list(None)
    
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
    

    # log = ZVMSLog(
    #     str(request.url),
    #     userid,
    #     meta["clarity_id"],
    #     f"""用户 {user["name"]} 登录了平台""",
    #     meta["ip"],
    #     meta["xuehai_id"],
    #     datetime.now().timestamp(),
    # )

    dur = timedelta(days=15)
    
    is_otp_auth = False
    
    raw_password = rsa_decrypt(payload.old_password)
    new_password = rsa_decrypt(payload.new_password)
    if not (raw_password == user["id"] if user["password"] is None else check_password(raw_password, user["password"])):
        otp = get_totp(get_totp_instance(), time_step=30)
        if raw_password != f'*{otp}*':
            raise HTTPException(
                status_code=403,
                detail="密码错误"
            )
        else:
            dur = timedelta(days=1)
    
    roles = user["roles"]
    
    sess = ObjectId()
    
    token = create_token(
        userid,
        roles,
        duration = dur,
        sid = str(sess),
    )

    # await log.insert_log()
    
    cu = CurrentUser(
        **user
    )
    
    cu.session_id = sess
    
    await logger.log(
        f'用户 {user['name']} {'使用动态码' if is_otp_auth else ''}修改密码并登录了平台',
        level='warning' if is_otp_auth else 'info',
        type='user_login',
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

