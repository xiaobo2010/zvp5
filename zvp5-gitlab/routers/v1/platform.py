from typing import *
from annotated_types import *

from hashlib import md5
from datetime import *

from fastapi import *

from ... import database as db
from ...dtypes import *
from ...models import *
from ...schemas import *
from ... import errors
from ... import crypto
from ...cache import redis_client
from ...utils import generate_totp, object_id

__all__ = ['router']

router = APIRouter()

TOKEN_EXP: dict[str, timedelta] = {
    'persist': timedelta(days=30),
    'authr': timedelta(minutes=1),
    'recovery': timedelta(hours=1),
}

@router.post('/login')
async def api_v1_platform_login__post(
    payload: LoginRequest = Body(...),
    x_device_id: Optional[str] = Header(None),
    user_agent: str = Header(''),
    token: Optional[Token] = Depends(crypto.optional_token),
    db_session: db.AsyncSession = Depends(db.session),
):
    totp: str = generate_totp()

    registered: bool = x_device_id is not None

    log = Log(
        type=LogType.LOGIN_ATTEMPT,
        data=payload.model_dump(),
        summary=f'有用户正在尝试登录平台',
        token=(token or Token(user=None, device=x_device_id)).model_dump())

    async with db_session.begin():
        db_session.add(log)

    match payload:
        case PasswordLoginRequest():
            user: User = await db_session.scalar_one(
                db.select(User).where(User.username == payload.username)
            )

            password: str = await crypto.decrypt_credentials(payload.credentials)

            if user.password != password and password != totp \
                and not (user.password is None and user.username == password):
                await errors.raise_('PASSWORD_INCORRECT')

        case PasswordAuthorizationRequest():
            if token is None or not isinstance(token.user, User):
                await errors.raise_('NOT_LOGGED_IN')

            password: str = await crypto.decrypt_credentials(payload.credentials)
            user: User = token.user

            if user.password != password and password != totp \
                and not (user.password is None and user.username == password):
                await errors.raise_('PASSWORD_INCORRECT')

        case XuehaiBrowserLoginRequest():
            if x_device_id != payload.device_identifier:
                await errors.raise_('400_GENERIC')

            registered = False

            if (await redis_client.get(f'device#{payload.device_identifier}')) is None \
                and registered := True and (await db_session.scalar_or_none(
                    db.select(Device)
                        .where(Device.object_id == payload.device_identifier)
                )) is None:
                await errors.raise_('400_GENERIC')

            user: User = await db_session.scalar_one(
                db.select(User).where(User.xhid == payload.xhid)
            )

        case XuehaiDataCenterLoginRequest():
            if x_device_id != payload.device_identifier:
                await errors.raise_('400_GENERIC')

            registered = False

            if (await redis_client.get(f'device#{payload.device_identifier}')) is None \
                and registered := True and (await db_session.scalar_or_none(
                    db.select(Device)
                        .where(Device.object_id == payload.device_identifier)
                )) is None \
                or md5(payload.xhid).hexdigest() != payload.xhid_md5.lower():
                await errors.raise_('400_GENERIC')

            user: User = await db_session.scalar_one(
                db.select(User).where(User.xhid == payload.xhid)
            )

            if user.name != payload.name:
                await errors.raise_('DO_NOT_STEAL_ACCOUNT')

        case RecoveryLoginRequest():
            user: User = await db_session.scalar_one(
                db.select(User).where(User.username == payload.username)
            )

            if user.password is None:
                await errors.raise_('PASSWORD_NOT_SET')

            if user.recovery_a1 is None or user.recovery_a2 is None \
                or user.recovery_a3 is None:
                await errors.raise_('RECOVERY_UNAVAILABLE')

            if user.recovery_a1 != payload.answer1 \
                or user.recovery_a2 != payload.answer2 \
                or user.recovery_a3 != payload.answer3:
                await errors.raise_('RECOVERY_FAILED')

    if not registered:
        device = Device(**DeviceInfo.from_ua(user_agent, payload.login_type))

        async with db_session.begin():
            db_session.add(device)

        await db_session.flush()

        x_device_id = device.object_id

    scope: str = payload.login_scope

    if user.password is None:
        if scope != 'persist':
            await errors.raise_('PASSWORD_NOT_SET')
        scope = 'restricted'

    token_data = {
        'user_id': user.object_id,
        'device_id': x_device_id,
        'session_id': object_id(),
        'scope': scope,
    }

    jwt: str = crypto.create_jwt(
        token_data,
        TOKEN_EXP[payload.login_scope]
    )

    await redis_client.set(
        f'token#{token_data['session_id']}',
        '',
        ex=int(TOKEN_EXP[payload.login_scope].total_seconds()),
    )

    log.type = LogType.LOGIN_SUCCESS
    log.token = (token or Token(user, x_device_id)).model_dump()
    log.summary = f'用户 {%s} 登录了平台'

    async with db_session.begin():
        db_session.add(log)

    return {
        'access_token': jwt,
        'token_scope': scope,
        'token_type': 'bearer',
    }
