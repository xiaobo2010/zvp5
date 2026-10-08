from typing import *
from annotated_types import *
from inspect import *

from sqlalchemy import *
from sqlalchemy.exc import *
from sqlalchemy.ext.associationproxy import *
from sqlalchemy.ext.asyncio import *
from sqlalchemy.ext.hybrid import *
from sqlalchemy.orm import *
from sqlalchemy.types import *

from pydantic import *
from pydantic_core.core_schema import *

from bson import ObjectId as _ObjectId

import json
import enum

from . import config
from . import dtypes

from collections import defaultdict

engine: AsyncEngine = create_async_engine(config.DATABASE_URI)

class Json(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, _):
        return json.dumps(value).encode('utf-8')

    def process_result_value(self, value: bytes, _):
        return json.loads(value.decode('utf-8'))

class ObjectId(TypeDecorator):
    impl = String(24)
    cache_ok = True

    def process_bind_param(self, value: str | _ObjectId, _):
        return str(_ObjectId(str(value)))

    def process_result_value(self, value: str, _):
        return value
    
class Hashed(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | dtypes.Hash, _):
        return value if isinstance(value, str) else value.digest

    def process_result_value(self, value: str, _):
        return dtypes.Hash(value)

class IntegralEnum(TypeDecorator):
    __enum__: type[enum.IntEnum] | type[enum.IntFlag]
    impl = Integer
    cache_ok = True

    def __init__(self, __enum__: type[enum.IntEnum] | type[enum.IntFlag], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__enum__ = __enum__
    
    def process_bind_param(self, value: int, _):
        return int(value)

    def process_result_value(self, value: int, _):
        if value is None:
            return value
        return self.__enum__(value)

class Model(DeclarativeBase):
    metadata: MetaData = MetaData(
        naming_convention = {
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )

    type_annotation_map: Mapping[Any, Type[TypeEngine[Any]] | TypeEngine[Any]] = {
        dict: Json,
        list: Json,
        str: Text,
    }

async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.create_all)

async def drop_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Model.metadata.drop_all)

session: async_sessionmaker[AsyncSession] = async_sessionmaker[AsyncSession](engine)

def RelationTable(name: str, table1: str, table2: str) -> Table:
    return Table(
        name,
        Model.metadata,
        Column('_internal1', ForeignKey(f'{table1}.object_id'), primary_key=True, index=True),
        Column('_internal2', ForeignKey(f'{table2}.object_id'), primary_key=True, index=True),
    )

class Schema(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra='ignore',
        revalidate_instances='always',
        hide_input_in_errors=True,
    )