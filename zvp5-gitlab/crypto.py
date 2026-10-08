import bcrypt
import config
from jose import jwt
from datetime import *

def check_password(hash: str, password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hash.encode('utf-8'))

def hash_password(password) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_jwt(data, expires: timedelta) -> str:
    return jwt.encode(
        data | {'exp': expires},
        config.JWT_SECRET_KEY,
        algorithm='HS256',
    )

async def _parse_token(
    token: str,
    db_session: db.AsyncSession,
    expected_scope: Literal['persist', 'authz', 'recovery'] = 'persist',
) -> Token:
    try:
        dic = jwt.loads(token, config.JWT_KEY, algorithms=('HS256', ))
    except JWTError:
        # todo: distinguish expired tokens and non-token strings
        await errors.raise_('INVALID_TOKEN')
    if (
        'device_id' not in dic
        or 'session_id' not in dic
        or 'scope' not in dic
        or dic['scope'] != expected_scope
    ):
        if expected_scope == 'authz' and dic.get('scope') == 'persist':
            await errors.raise_('AUTHZ_NEEDED')
        await errors.raise_('INVALID_TOKEN')
    if 'user_id' not in dic:
        user = Token.SYSTEM.user
    else:
        from .models import User
        user = User.schema.model_validate(await db_session.scalar_one(
            db.select(User).where(User.object_id == dic['user_id'])
        ))

    from .models import Device

    return Token.model_construct(
        device=Device.schema.model_validate(await db_session.scalar_one(
            db.select(Device).where(Device.object_id == dic['device_id'])
        )),
        user=user,
        session_id=dic['session_id'],
        is_system='user_id' not in dic,
        scope=dic['scope'],
    )

async def token(
    authorization: str = Header(...),
    db_session: db.AsyncSession = Depends(db.session),
) -> Token:
    return _parse_token(authorization, db_session)

async def optional_token(
    authorization: Optional[str] = Header(None),
    db_session: db.AsyncSession = Depends(db.session),
) -> Optional[Token]:
    if authorization is None:
        return None
    return _parse_token(authorization, db_session)

async def authz_token(
    authorization: str = Header(...),
    db_session: db.AsyncSession = Depends(db.session),
) -> Token:
    return _parse_token(authorization, db_session, 'authz')

async def decode_credentials(encrypted: str) -> DecodedCredentials:
    raise NotImplementedError
