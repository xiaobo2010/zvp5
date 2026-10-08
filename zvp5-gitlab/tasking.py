from typing import *
from annotated_types import *
from collections.abc import Callable, Coroutine

from asyncio import *

from fastapi import *

from .dtypes import *
from .schemas import *
from . import crypto
from . import database as db
from .models import SavedTask

type _OnProgress = Callable[[float], Coroutine]
type TaskFunc = Callable[[Token, db.AsyncSession, _OnProgress], Coroutine]

class TaskManager:
    '''Use `taskmgr: TaskManager = Depends()` to get an instance.'''

    __bgtasks: BackgroundTasks
    __token: Token

    def __init__(
        self,
        background_tasks: BackgroundTasks,
        token: Token = Depends(crypto.token)
    ):
        self.__bgtasks = background_tasks
        self.__token = token

    async def run_task(self, name: str, task_func: TaskFunc, *, timeout: int | float):
        '''Example: `result = await taskmgr.run_task('task', foo, 2)`'''

        task_obj = SavedTask(name=name, user_id=token_data["user_id"])

        async def wrapped_task():
            async with db.session_factory() as db_session:
                async with db_session.begin():
                    db_session.add(task_obj)
                await db_session.flush()

                async def callback(progress: float):
                    task_obj.progress = progress
                    async with db_session.begin():
                        db_session.add(task_obj)
                    await db_session.flush()

                try:
                    result = await task_func(self.__token, db_session, callback)
                    task_obj.result = result
                    task_obj.status = TaskStatus.DONE
                except BaseException as ex:
                    task_obj.result = str(ex)
                    task_obj.status = TaskStatus.FAILED

                async with db_session.begin():
                    db_session.add(task_obj)
                await db_session.flush()

        self.__bgtasks.add_task(wrapped_task)
        await asyncio.sleep(timeout)
        return SavedTask.schema.model_construct(task_obj) # take a snapshot

class PushService:
    __bgtasks: BackgroundTasks
    __conns: ClassVar[dict[str, WebSocket]] = {}

    def __init__(self, background_tasks: BackgroundTasks):
        self.__bgtasks = background_tasks

    @staticmethod
    async def on_connect(user: str, ws: WebSocket):
        await ws.accept()
        __conns[user] = ws
        async with db.session() as db_session:
            async with db_session.begin():
                try:
                    async for cached in await db_session.stream_scalars(
                        db.select(Notification).where(Notification.user_id == user)
                    ):
                        await ws.send_json(cached.data)
                        db_session.delete(cache)
                    await ws.send_json({'type': 'platform.sync.completed'})
                except WebSocketDisconnect: # disconnected before sync complete
                    del __conns[user]
                    await db_session.rollback() # don't delete data, wait till next time
                    raise # re-raise, so the caller will know the connection has died

    @staticmethod
    def on_disconnect(user: str):
        del __conns[user]

    async def notify_all(users: Iterable[str], payload: dict[str, Any]):
        async def task():
            async with db.session() as db_session:
                async with db_session.begin():
                    def factory(user: str):
                        async def coro():
                            if (ws := __conns.get(user)) is not None:
                                try:
                                    await ws.send_json(payload)
                                    return
                                except WebSocketDisconnect:
                                    pass
                            db_session.add(Notification(user_id=user, data=payload))
                        return coro
                    await asyncio.gather(map(factory, users))
        self.__bgtasks.add_task(task)
