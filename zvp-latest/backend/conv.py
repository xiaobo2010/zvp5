#! /usr/local/bin/python3.15

from database import db, connect_to_mongo, close_mongo_connection
from pydantic import BaseModel, ConfigDict, Field
from contextlib import asynccontextmanager
from bson import ObjectId
from functools import reduce
from asyncio import run
from typing import Any, Optional

__all__ = ['upgrade', 'rollback']

@asynccontextmanager
async def connect_db():
    print('> Connecting')
    await connect_to_mongo()
    try:
        yield
    finally:
        print('> Disconnecting')
        await close_mongo_connection()


class Model(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)


class UserV1(Model):
    object_id: ObjectId = Field(alias="_id")
    id: int | str
    name: str
    group: list[str]
    password: str
    past: Optional[list[str]] = None # discarded
    sex: Optional[str] = None # discarded
    eligibility: Optional[list[Any]] = None # discarded


class GroupV1(Model):
    object_id: ObjectId = Field(alias="_id")
    type: str
    name: str
    permissions: list[str]
    description: Optional[str] = None # discarded


async def upgrade():
    async with connect_db():
        old = db.zvms
        new = db.zvms_new
        
        print('> Clearing old data')
        await new.groups.drop()
        await new.users.drop()
        
        print('> Fetching groups')
        raw_groups = map(GroupV1.model_validate, await old.groups.find({}).to_list(None))
        groups = []
        perms = {}
        for i in raw_groups:
            if i.type == 'permission':
                perms[str(i.object_id)] = i
            else:
                groups.append(i)
        del raw_groups
        print('  Loaded', len(groups), 'regular groups,', len(perms), 'permission groups')
        
        print('> Fetching users')
        users = list(map(UserV1.model_validate, await old.users.find({}).to_list(None)))
        print('  Loaded', len(users), 'users')
        
        print('> Transforming groups')
        new_groups = [{
            "_id": group.object_id,
            "name": group.name,
        } for group in groups]
        
        print('> Writing groups')
        await new.groups.insert_many(new_groups, ordered=False)
        
        print('> Transforming users')
        new_users = [{
            "_id": user.object_id,
            "id": str(user.id),
            "name": user.name,
            "roles": list(filter(
                lambda x: x != 'student',
                set(reduce(
                    lambda x, y: x.__iadd__(y),
                    (perms[role].permissions for role in user.group if role in perms),
                    []
                ))
            )),
            "class_id": (
                lambda x: ObjectId(x[0]) if len(x) else None
            )(list(x for x in user.group if x not in perms)[:1]),
            "password": user.password,
            "session_id": None,
        } for user in users]
        
        print(len(list(x for x in new_users if not x["class_id"])), 'users have no class_id')
        
        print('> Writing users')
        await new.users.insert_many(new_users, ordered=False)


async def clear():
    dbase = db.zvms_new
    await dbase.users.drop()
    await dbase.groups.drop()


if __name__ == '__main__':
    run(upgrade())

