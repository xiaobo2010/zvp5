from typing import *
from annotated_types import *

from datetime import *

from pydantic import *
from pydantic.validators import *

from .dtypes import *

from .database import Schema
from .models import *

class PasswordLoginRequest(Schema):
    credentials: Annotated[str, Field(pattern='^[0-9A-Za-z/+]+={0,2}$')]
    login_scope: Literal['persist']
    login_type: Literal['password_authz']
    username: str

class XuehaiBrowserLoginRequest(Schema):
    # credentials: Annotated[str, Field(pattern='^[0-9A-Za-z/+]+={0,2}$')]
    device_identifier: str # pre-fetched using customAjax() or stored
    login_scope: Literal['persist']
    login_type: Literal['xhbrowser']
    xhid: int

class XuehaiDataCenterLoginRequest(Schema):
    class_name: str
    # credentials: Annotated[str, Field(pattern='^[0-9A-Za-z/+]+={0,2}$')]
    device_identifier: str
    login_scope: Literal['persist']
    login_type: Literal['xhdatacenter']
    name: str
    xhid: int
    xhid_md5: Annotated[str, Field(pattern='^[0-9A-Za-z/+]{32}$')]

class PasswordAuthorizationRequest(Schema):
    credentials: Annotated[str, Field(pattern='^[0-9A-Za-z/+]+={0,2}$')]
    login_scope: Literal['authr']
    login_type: Literal['password_authr']

class BearerTokenLoginRequest(Schema):
    login_scope: Literal['persist', 'recovery']
    login_type: Literal['token']

class RecoveryLoginRequest(Schema):
    answer1: str
    answer2: str
    answer3: str
    login_scope: Literal['recovery']
    login_type: Literal['recovery']
    username: str

type LoginRequest = Annotated[
    PasswordLoginRequest |
    PasswordAuthorizationRequest |
    XuehaiBrowserLoginRequest |
    XuehaiDataCenterLoginRequest |
    BearerTokenLoginRequest |
    RecoveryLoginRequest,
    Field(discriminator='login_type')
]

class DecodedCredentials(Schema):
    password: str
    timestamp: datetime

class RecoveryQuestionEntry(Schema):
    question: str
    answer: str

class WorkflowApproveRequest(Schema):
    action: Literal['approve']
    comment: Optional[str]

class WorkflowDisapproveRequest(Schema):
    action: Literal['reject', 'deny', 'cancel']
    comment: str

class WorkflowUpdateTitleRequest(Schema):
    action: Literal['update_title']
    title: str

class WorkflowUpdateActivityInfoRequest(Schema):
    action: Literal['update_activity_info']
    title: str
    description: str

class UpdatedMemeber(Schema):
    action: Literal['new', 'update']
    member: ActivityMember.upload_schema

class DeletedMember(Schema):
    action: Literal['delete']
    member: Annotated[str, Field(pattern='^[0-9A-Fa-f]{24}$')]

type ModifiedMember = Annotated[
    UpdatedMember |
    DeletedMember,
    Field(discriminator='action')
]

class WorkflowUpdateActivityMembersRequest(Schema):
    action: Literal['update_activity_members']
    members: list[ModifiedMember]

type WorkflowUpdateRequest = Annotated[
    WorkflowApproveRequest |
    WorkflowDisapproveRequest |
    WorkflowUpdateTitleRequest |
    WorkflowUpdateActivityInfoRequest |
    WorkflowUpdateActivityMembersRequest,
    Field(discriminator='action')
]
