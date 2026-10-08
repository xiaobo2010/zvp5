import utils
from collections import defaultdict
from typing import NoReturn

_errormap: dict[str, dict] = defaultdict({
    # 4xx responses
    'DATA_CONFLICT': {
        'status_code': 409,
        'detail': '数据存在冲突',
    },
    'BAD_PAGING': {
        'status_code': 416,
        'detail': '页码不存在',
    },
    'BAD_PAYLOAD': {
        'status_code': 422,
        'detail': '提交数据格式有误',
    },
    'BAD_MIME': {
        'status_code': 415,
        'detail': '文件类型错误',
    },
    # 401 responses
    'NEED_LOGIN': {
        'status_code': 401,
        'detail': '未登录',
    },
    'SESSION_EXPIRED': {
        'status_code': 401,
        'detail': '登录已过期，请重新登录',
    },
    'DEVICE_CHANGED': {
        'status_code': 401,
        'detail': '您的账号已在其他设备登录，请重新登录',
    },
    'LOGGED_OUT': {
        'status_code': 401,
        'detail': '已注销，请重新登录',
    },
    # 403 responses
    'NO_PERM': {
        'status_code': 403,
        'detail': '权限不足',
    },
    'SYS_ONLY': {
        'status_code': 403,
#
        'detail': '请勿访问系统管理接口',
    },
    'REQUIRE_AUTH': {
        'status_code': 403,
        'detail': '请先确认您的密码以授权本操作',
    },
    'REQUIRE_USER_INIT': {
        'status_code': 403,
        'detail': '请先完善用户信息',
    },
    'UNSAFE_LOGIN': {
        'status_code': 403,
        'detail': '登录状态异常，无法进行操作',
    },
    'DEVICE_BANNED': {
        'status_code': 403,
        'detail': '设备存在异常流量，禁止访问',
    },
    'USER_BANNED': {
        'status_code': 403,
        'detail': '用户存在违规行为，禁止访问',
    },
    'MANDATE_PASSWD': {
        'status_code': 403,
        'detail': '您的密码不符合安全标准，请先修改密码',
    },
    'NO_SELF_EDIT': {
        'status_code': 403,
        'detail': '不能修改自己的班级或权限',
    },
    'NO_SELF_AUDIT': {
        'status_code': 403,
        'detail': '不能审核自己创建的义工',
    },
    # 404 responses
    '404_GENERIC': {
        'status_code': 404,
        'detail': '访问的接口或资源不存在',
    },
    '404_ACTIVITY': {
        'status_code': 404,
        'detail': '义工不存在',
    },
    '404_TEMPLATE': {
#
        'status_code': 404,
        'detail': '义工模板不存在',
    },
    '404_SCHOOL': {
        'status_code': 404,
        'detail': '学校不存在',
    },
    '404_CLASS': {
        'status_code': 404,
        'detail': '班级不存在',
    },
    '404_USER': {
        'status_code': 404,
        'detail': '用户不存在',
    },
    # 400 responses
    'BAD_PARAM': {
        'status_code': 400,
        'detail': '参数错误',
    },
    'WRONG_PASSWD': {
        'status_code': 400,
        'detail': '密码错误',
    },
    'WRONG_CAPCHA': {
        'status_code': 400,
        'detail': '验证码错误',
    },
    'WRONG_ANSWER': {
        'status_code': 400,
        'detail': '密保问题回答错误',
    },
    # 5xx responses
    'EXCEPTION': {
        'status_code': 500,
        'detail': '不好了，服务器罢工了',
    },
    'UPLOAD_FAILURE': {
        'status_code': 502,
        'detail': '文件服务配置错误，上传失败',
    },
    'MAINTENANCE': {
        'status_code': 503,
        'detail': '服务器维护中',
    },
    'AUTO_UPGRADE': {
        'status_code': 503,
#
        'detail': '服务器自动升级中',
    },
}, default={
    'status_code': 400,
    'detail': '未知错误',
})

def raise_(key: str) -> NoReturn:
    '''TODO: doc'''
    raise NotImplementedError

@utils.module_init
def __module_init__() -> None:
    from fastapi import HTTPException

    @utils.export
    def raise_(key: str) -> NoReturn:
        raise HTTPException(**_errormap[str(key).upper()])