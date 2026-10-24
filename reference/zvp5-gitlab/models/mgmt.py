from typing import *
from annotated_types import *

from ..dtypes import (
    LogType,
    LogLevel,
    DeviceType,
)
from .shared import *

__all__ = ['Device', 'Log']

class Device(db.Model):
    __tablename__ = 'devices'

    #### TODO: move schemas.Device here as 'upload_schema' and fill cols
    '''
class DeviceInfo(pydantic.BaseModel):
    model: str = 'unknown'
    os: str = 'unknown'
    browser: str = 'unknown'

    @staticmethod
    def from_ua(ua: str, additional_hint: Optional[str] = None):
        return DeviceInfo() # STUB FUNCTION
    '''

class Log(db.Model):
    __tablename__ = 'logs'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    type: db.Mapped[LogType] = _ienumcol(
        LogType, default=LogType.GENERIC
    )
    level: db.Mapped[LogLevel] = _ienumcol(
        LogLevel, default=LogLevel.INFO
    )
    summary: db.Mapped[str] = _col()
    data: db.Mapped[dict] = _jsoncol() # hmm... NoSQL style
    token: db.Mapped[dict] = _jsoncol()
    timestamp: db.Mapped[int] = _ctimecol()

    # schemas #
    class short_schema(db.OrmSchema):
        object_id: str
        type: LogType
        level: LogLevel
        summary: str
        token: dict
        timestamp: int

        _coerce = utils.coerce_ienum(type=LogType, level=LogLevel)
        _ser = utils.serialize_ienum(type=LogType, level=LogLevel)

    class schema(db.OrmSchema):
        object_id: str
        type: LogType
        data: str # the only diff is 'data' -- sometimes a lot of data
        token: dict
        timestamp: int

        _coerce = utils.coerce_ienum(type=LogType, level=LogLevel)
        _ser = utils.serialize_ienum(type=LogType, level=LogLevel)
