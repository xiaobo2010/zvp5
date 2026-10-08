from pydantic import *
from pydantic_core.core_schema import *

import enum
from typing import *
from annotated_types import *

from .. import database as db

__all__ = ['LogLevel', 'LogType']

class LogLevel(enum.IntEnum):
    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4
    FATAL = 5 # rarely used

class LogType(enum.IntEnum):
    # general
    GENERIC_TRACE = 0x0000
    GENERIC_DEBUG = 0x0001
    GENERIC_INFO = 0x0002
    GENERIC_WARNING = 0x0003
    GENERIC_ERROR = 0x0004
    GENERIC_FATAL_ERROR = 0x0005

    # fatal errors
    FATAL_SRC_CORRPUT = 0x0100
    FATAL_DB_CORRUPT = 0x0101
    FATAL_PORT_OCCUPIED = 0x0102
    FATAL_NODEJS_ERROR = 0x0103
    FATAL_DOS_PROTECT = 0x0104
    FATAL_REDIS_ABSENT = 0x0105
    FATAL_DISC_FULL = 0x0106
    FATAL_SIGTERM = 0x0107

    # in-dev, DEBUG/TRACE level
    INDEV_CHECKPOINT = 0x0200
    INDEV_EXCEPTION = 0x0201
    INDEV_TRACEBACK = 0x0202
    INDEV_SQL_EXEC = 0x0203
    INDEV_REDIS_QUERY = 0x0204

    # access control, WARNING level
    ACC_INSUFFICIENT_PERM = 0x0300
    ACC_UNAUTHENTICATED = 0x0301
    ACC_UNAUTHORIZED = 0x0302
    ACC_EXPLOIT_ATTEMPT = 0x0303 # ERROR level
    ACC_ACCOUNT_THEFT = 0x0304 # ERROR level
    ACC_WRONG_PASSWORD = 0x0305 # DEBUG/WARNING dep on cnt
    ACC_INSECURE_PASSWORD = 0x0306 # pswd like Aa123456 or initial
    ACC_VIOLATION = 0x03FF # ERROR level, log when user gets banned

    # SYSTEM actions, WARNING level
    SYS_IMPORT_DB = 0x0400
    SYS_EXPORT_DB = 0x0401
    SYS_CLEAR_GRADS = 0x0402
    SYS_UPGRADE = 0x0403

    # users
    USER_CREATE = 0x1000
    USER_MODIFY = 0x1001
    USER_DELETE = 0x1002
    USER_PASSWORD_CHANGE = 0x1003
    USER_PASSWORD_RESET = 0x1004
    USER_RECOVERY_CHANGE = 0x1005
    USER_LOGIN = 0x10A0
    USER_LOGOUT = 0x10A1
    USER_FORCE_LOGOUT = 0x10A2 # later when added max device count
    USER_BIND_XH = 0x10A3

    # activities
    ACTIVITY_CREATE = 0xA000
    ACTIVITY_MODIFY = 0xA001
    ACTIVITY_DELETE = 0xA002
    ACTIVITY_AUDIT = 0xA003
    ACTIVITY_MERGE = 0xA004
    MEMBER_ADD = 0xA100
    MEMBER_MODIFY = 0xA101
    MEMBER_REMOVE = 0xA102

    # HTTP codes, DEBUG level
    HTTP_200 = 0xE200 # TRACE level, indicating GET requests only
    HTTP_400 = 0xE400
    HTTP_401 = 0xE401
    # HTTP_403 is access violation, see above
    HTTP_404 = 0xE404
    HTTP_405 = 0xE405 # this is WARNING level, malicious request
    HTTP_409 = 0xE409
    HTTP_410 = 0xE410
    HTTP_416 = 0xE416
    HTTP_422 = 0xE422
    HTTP_429 = 0xE429 # also suspicious, debounce is already implemented
    HTTP_451 = 0xE451 # this code is perfect for taboo words ·_·
    HTTP_503 = 0xE503
    WS_1008 = 0xEA08

    # workflows
    FLOW_CREATE = 0xF001
    FLOW_MODIFY_TITLE = 0xF002
    FLOW_ADD_CHANGE = 0xF003
    FLOW_APPROVE = 0xF004
    FLOW_DENY = 0xF005
    FLOW_CANCEL = 0xF006
    FLOW_REJECT = 0xF007
    FLOW_MERGE = 0xF008 # reserved