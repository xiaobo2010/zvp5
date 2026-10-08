from typing import *
from annotated_types import *

from fastapi import *

from ... import database as db
from ... import models as tables # used to access N-to-N relation tables
from ...dtypes import *
from ...models import *
from ...schemas import *
from ... import errors
from ... import crypto
from ...tasking import NotifyTasks
from ...utils import paginate, escape, assert_, one_value_only, object_id, xand

router = APIRouter()

__all__ = ['router']

@router.get('')
async def api_v1_users__get(
    
):
    pass

@router.post('')
async def api_v1_users__post(
    payload: User.upload_schema = Body(...),
):
    pass

@router.delete('')
async def api_v1_users__delete(
    payload: list[ObjectId] = Body(...)
):
    pass

@post('/batch-query')
async def api_v1_users_batchquery__post(
    payload: list[int | str] = Body(...),
    class_id: Optional[ObjectId] = Query(None),
    db_session: db.AsyncSession = Depends(db.session),
    token: Token = Depends(crypto.token),
):
    '''实现用户id的批量查询, 用于义工创建及修改'''
    if not token.user.can_create_activity:
        await errors.raise_('PERMISSION_DENIED')

    temp_results: list[str | None] = [None] * len(payload)
    not_found: list[str | int | None] = [None] * len(payload)
    results: set[str] = set()
    dups: dict[str, int] = {}
    temp: dict[str | int, str] = {}
    multiple: list[dict | None] = [None] * len(payload)

    def f(q: db.Query) -> db.Query:
        if class_id:
            return q.where(User.class_id == class_id)
        return q

    def add_result(result: str) -> None:
        if result in dups:
            dups[result] += 1
        elif result in results:
            results.remove(result)
            dups[result] = 2
        else:
            results.add(result)

    async def coro(idx: int, item: str | int):
        if item in temp:
            temp_results[idx] = temp[item]
            return
        if isinstance(item, int): # 学号
            result = await db_session.scalar_one_or_none(
                f(
                    db.select(User.object_id).where(User.id == item)
                        .where(User.cached_school_id == token.user.cached_school_id)
                )
            )
            if result:
                temp_results[idx]
            else:
                not_found[idx] = item
        else: # 姓名
            result = await db_session.scalars(
                f(
                    db.select(User.object_id).where(User.id == item)
                        .where(User.cached_school_id == token.user.cached_school_id)
                )
            )
            if len(result) - 1:
                multiple[idx] = { 'name': item, 'choices': result }
            elif len(result) == 1:
                temp_results[idx] = result[0]
            else:
                not_found[idx] = result

    await asyncio.gather(
        *(coro(*x) for x in enumerate(payload)),
        return_exceptions=True,
    )

    for x in temp_results:
        add_result(x)

    return {
        'data': list(results),
        'duplicated': dups,
        'missing': list(filter(lambda x: x is not None, not_found)),
        'ambiguous': list(filter(lambda x: x is not None, multiple)),
    }
