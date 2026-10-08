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

type _stat = Annotated[
    ActivityStatus,
    pydantic.fields.BeforeValidator(utils.coerce_ienum_raw(ActivityStatus)),
]

ANY_STATUS = [
    ActivityStatus.EFFECTIVE,
    ActivityStatus.REJECTED,
    ActivityStatus.INVALID,
]

@router.get('')
async def api_v1_activities__get(
    class_id: Optional[ObjectId] = Query(None),
    date_end: Optional[str] = Query(None),
    date_start: Optional[int] = Query(None),
    deleter_id: Optional[ObjectId | Literal['all']] = Query(None),
    member_id: Optional[ObjectId] = Query(None),
    order: Literal['asc', 'desc'] = Query('desc'),
    page: Annotated[int, Gt(0)] = Query(...),
    parent_activity_id: Optional[ObjectId] = Query(None),
    query: Optional[str] = Query(None),
    school_id: Optional[ObjectId] = Query(None),
    size: Annotated[int, Gt(0), Le(100)] = Query(...),
    status: list[_stat] | _stat = Query(default_factory=lambda: ANY_STATUS[:]),
    template_id: Optional[ObjectId] = Query(None),
    db_session: db.AsyncSession = Depends(db.session),
    token: Token = Depends(crypto.token),
):
    if date_end <= date_start:
        await errors.raise_('NEGATIVE_DATE_RANGE')

    if sum(map(bool, (member_id, class_id, school_id))) > 1:
        await errors.raise_('400_GENERIC')

    if not isinstance(status, list):
       status = [status]
    if (
        not len(status)
        or not all((x in ANY_STATUS) for x in status)
        and status != [ActivityStatus.DRAFT]
    ):
        await errors.raise_('400_GENERIC')

    if token.is_system: # don't check UserRole.SYSTEM, it's a marker only
        pass
    elif (
        not any((member_id, class_id, school_id))
        or deleter_id and deleter_id != token.user.object_id
    ):
        await errors.raise_('PERMISSION_DENIED')
    elif (
        not token.user.role
        or status[0] not in ANY_STATUS
        and UserRole.ADMIN not in token.user.role
    ):
        if (
            not token.user.role and status[0] not in ANY_STATUS
            or school_id
            or class_id
            or member_id and member_id != token.user.object_id
        ):
            await errors.raise_('PERMISSION_DENIED')
    elif (
        UserRole.ADMIN in token.user.role
        or UserRole.STU_UNION in token.user.role
    ):
        if (
            school_id and school_id != token.user.cached_school_id
            or class_id and (await db_session.scalar(
                db.select(Class.school_id).where(Class.object_id == object_id)
            )) != token.user.cached_school_id
            or member_id and (await db_session.scalar(
                db.select(User.cached_school_id).where(User.object_id == member_id)
            )) != token.user.cached_school_id
        ):
            await errors.raise_('PERMISSION_DENIED')
    elif UserRole.SECRETARY in token.user.role:
        if (
            school_id
            or class_id and class_id != token.user.class_id
            or member_id and (await db_session.scalar(
                db.select(User.class_id).shere(User.object_id == member_id)
            )) != token.user.class_id
        ):
            await errors.raise_('PERMISSION_DENIED')
    else: # hackers may try to grant SYSTEM perm, but may end here instead
        await errors.raise_('PERMISSION_DENIED')

    def gen_query(q: db.Query) -> db.Query:
        if class_id or member_id:
            # we put group_by() here, because select(Activity).group_by(Activity)
            # makes no sense, and 'having' is the same as 'where' when not grouped
            q = q.join(
                ActivityMember,
                onclause=ActivityMember.activity_id == Activity.object_id,
            ).group_by(Activity)

        if class_id: q = q.where(ActivityMember.cached_class_id == class_id)
        if date_end: q = q.having(Activity.create_time < date_end)
        if date_start: q = q.having(Activity.create_time >= date_start)
        if deleter_id != 'all': q = q.having(Activity.deleter_id == deleter_id)
        if member_id: q = q.where(ActivityMember.user_id == member_id)
        if query: q = q.having(Activity.name.like(f'%{utils.escape_str(query)}%'))
        if school_id: q = q.having(Activity.school_id == school_id)

        return q.having(
            Activity.parent_activity_id == parent_activity_id
        ).having(
            Activity.status.in_(*map(int, status))
        )

    total: int = await db_session.scalar(
        gen_query(db.select(db.func.count()).select_from(Activity))
    ) or 0

    if member_id:
        def generate_result(activity: Activity, member: ActivityMember):
            return Activity.short_schema.model_validate(activity).model_dump() \
                | {'me': ActivityMember.schema.model_validate(member)}
    else:
        generate_result = Activity.short_schema.model_validate

    return {
        'data': [
            generate_result(*entry)
            async for entry in await db_session.stream(
                gen_query(db.select(*(
                    (Activity, ActivityMember) if member_id else (Activity, )
                )).select_from(Activity)).order_by(
                    getattr(Activity.object_id, order)(),
                ).limit(size).offset(page * size - size)
            )
        ]
        'metadata': {
            'total': total,
            'last': page * size >= total,
        },
    }

@router.post('')
async def api_v1_activities__post(
    payload: Activity.upload_schema = Body(...),
    token: Token = Depends(crypto.token),
    taskmgr: TaskManager = Depends(),
    pushsvc: PushService = Depends(),
):
    if not token.user.can_create_activities:
        await errors.raise_('PERMISSION_DENIED')

    # —— TODO in tasking.py ——————————————————————–-
    # 1. impl PushService
    # 2. optimize set_progress() callback: use cell, only update db after timeout
    # ——————————————————————————————————

    async def task(set_progress: TaskCallback, db_session: db.AsyncSession):
        template: ActivityTemplate | None = await db_session.scalar(
            db.select(ActivityTemplate).where(
                ActivityTemplate.object_id == payload.template_id
            )
        )

        if template is None:
            await errors.raise_('TEMPLATE_NOT_FOUND')

        if not any(perm in template.permission for perm in token.user.role):
            await errors.raise_('PERMISSION_DENIED')

        if not all(mem.duration <= template.max_time):
            await errors.raise_('TEMPLATE_VALIDATION_FAILED')

        set_progress(0)

        flow_id = None
        activity_oid = utils.object_id()
        activity = None

        async with db_session.begin():
            db_session.add(Log(
                type=LogType.ACTIVITY_CREATE,
                level=LogLevel.INFO,
                summary=f'用户 {token.user.name} 创建了义工 "{payload.name}"',
                data=(payload_json := payload.model_dump(exclude={
                    'draft', 'audit_requests',
                })) | {
                    'draft': payload.draft,
                    'object_id': activity_oid,
                    'school_id': token.user.cached_school_id,
                },
                token=token.model_dump(),
            ))

            db_session.add(activity := Activity(
                object_id=activity_oid,
                school_id=token.user.cached_school_id,
                status=ActivityStatus.DRAFT,
                **payload.model_dump(exclude={
                    'members',
                    'attachments',
                    'child_activities',
                    'draft',
                    'audit_requests',
                }),
            ))

            member_cnt = len(payload.members)

            if draft:
                total_cnt = member_cnt + len(payload.attachments)
            else:
                total_cnt = member_cnt + len(payload.attachments) \
                    + len(payload.child_activities)

            for idx, member in enumerate(payload.members):
                db_session.add(ActivityMember(
                    activity_id=acitivity_oid,
                    user_id=x.user_id,
                    pending_duration=x.duration,
                    effective_duration=AcitivityTime(
                        on_campus=0.0,
                        off_campus=0.0,
                        social_practice=0.0,
                    ),
                ))
                set_progress(99 * (idx + 1) // total_cnt)

            nfiles = 0

            async for file in await db_session.stream_scalars(
                db.select(Resource).where(Resource.object_id.in_(attachments))
            ):
                activity.attachments.add(file)
                nfiles += 1
                set_progress(99 * (member_cnt + nfiles) // total_cnt)

            if nfiles < len(payload.attachments):
                await errors.raise_('INVALID_ATTACHMENT')

            if not payload.draft:
                flow_oid = utils.object_id

                db_session.add(Workflow(
                    object_id=flow_oid,
                    activity_id=activity_oid,
                    creator_id=token.user.object_id,
                    cache_school_id=token.user.cache_school_id,
                    title=f'新义工: {payload.name}',
                    description=payload.description,
                    is_initial=True,
                ))

                db_session.add(WorkflowItem(
                    workflow_id=flow_oid,
                    type=WorkflowItemType.NEW_ACTIVITY,
                    data=payload_json,
                ))

                children = 0

                async for child in await db_session.stream_scalars(
                    db.select(Activity).where(
                        Activity.object_id.in_(payload.child_activities)
                    )
                ):
                    if child.parent_activity_id is not None: # conflict (unlikely), bail
                        await errors.raise_('DATA_CONFLICT')

                    child.parent_activity_id = activity_oid
                    db_session.add(child)
                    children += 1
                    set_progress(99 * (member_cnt + children + nfiles) // total_cnt)

                if children < len(payload.child_activities):
                    await errors.raise_('ACTIVITY_NOT_FOUND')

        async def update_cache(_, db_session_: db.AsyncSession):
            async with db_session_.begin():
                async for member, clazz, school in await db_session_.stream(
                    db.select(
                        ActivityMember, User.class_id, User.cached_school_id
                    ).select_from(
                        ActivityMember
                    ).join(
                        User, onclause=ActivityMember.user_id == User.object_id
                    ).where(
                        ActivityMember.activity_id == activity_oid
                    )
                ):
                    member.cached_class_id = clazz
                    member.cached_school_id = school
                    db_session_.add(member)

        await taskmgr.run_task(update_cache, timeout=0, token=Token.SYSTEM)

        if not payload.draft:
            await PushService.notify_all(
                payload.audit_requests,
                {
                    'title': '有新的义工等待审核',
                    'content': f'{token.user.name} 请求审核义工 "{payload.name}"',
                    'type': 'activity.audit.request',
                    'link': f'/workflows/{flow_oid}',
                },
            ) # immediate return

        set_progress(100)

        return f'/activities/view/{activity_oid}'

    result = await taskmgr.run_task(task, token=token)

    # task returns url but we need only the oid
    return JSONResponse(
        status_code=201 if type(result) is str else 202,
        content=result.split('/')[-1] if type(result) is str else result,
    )

@router.delete('')
async def api_v1_activities__delete(
    payload: list[ObjectId] = Body(...),
    token: Token = Depends(crypto.authz_token),
    taskmgr: TaskManager = Depends(),
):
    if not len(payload) or sorted(set(payload)) != sorted(payload):
        await errors.raise_('400_GENERIC')

    async def task(set_progress: TaskCallback, db_session: db.AsyncSession):
        acs: dict[str, Activity] = {
            i: a async for i, a await db_session.stream(
                db.select(Activiy.object_id, Activity).where(
                    Activity.object_id.in_(payload)
                )
            )
        }
        perma: dict[str, Activity] = {}

        if (length := len(acs)) != len(payload):
            await errors.raise_('ACTIVITY_NOT_FOUND')

        set_progress(cnt := 0)

        async for id, x in await db_session.stream(
            db.select(
                Workflow.activity_id,
                db.func.count(Workflow.object_id),
            ).where(
                Workflow.status == WorkflowStatus.PENDING
            ).group_by(
                Workflow.activity_id
            ).having(
                Workflow.activity_id.in_(payload)
            )
        ):
            if x:
                del acs[id]

        for key, val in list(acs.items()):
            if val.deleter_id:
                del acs[key]
                if (
                    UserRole.ADMIN not in token.user.role
                    and val.deleter_id != token.user.object_id
                ):
                    await errors.raise_('PERMISSION_DENIED')
                perma[key] = val

        async with db_session.begin():
            dumped_token = token.model_dump()
            if len(acs):
                db_session.add(Log(
                    type=LogType.ACTIVITY_DELETE,
                    level=LogLevel.INFO,
                    summary=f'用户 {token.user.name} 删除了 {len(acs)} 个义工',
                    data={
                        'lst': [Activity.schema.model_validate(x) for x in acs.values()],
                        'permanent': false,
                    },
                    token=dumped_token,
                ))
            if len(perma):
                db_session.add(Log(
                    type=LogType.ACTIVTY_DELETE,
                    level=LogLevel.WARNING,
                    summary=f'用户 {token.user.name} 永久删除了 {len(perma)} 个义工',
                    data={
                        'lst': [Activity.schema.model_validate(x) for x in perma.values()],
                        'permanent': true,
                    },
                    token=dumped_token,
                ))

            for x in acs.values():
                x.deleter_id = token.user.object_id
                db_session.add(x)
                set_progress(50 * (cnt := cnt + 1) // length)

            for x in perma.values():
                db_session.delete(x)
                set_progress(50 * (cnt := cnt + 1) // length)

        set_progress(100 * cnt // length)

        return f'{cnt}/{length}'

    result = await taskmgr.run_task(task)

    return JSONResponse(
        status_code=200 if type(result) is str else 202,
        content=(
            dict(zip(('success', 'total'), map(int, resulr.split('/'))))
            if type(result) is str
            else result
        ),
    )

@router.get('/{activity_id}')
async def api_v1_activities___get(
    activity_id: ObjectId = Path(...),
    member_id: Optional[ObjectId] = Query(None),
    db_session: db.AsyncSession = Depends(db.session),
    token: Token = Depends(crypto.token),
):
    #...+ELLIPSIS (validation logic goes here)

    if member_id:
        activity: Activity
        member: ActivityMember
        activity, member = (await db_session.execute(
            db.select(Activity, ActivityMember).select_from(ActivityMember).join(
                Activity, onclause=Activity.object_id == ActivityMember.activity_id
            ).where(
                ActivityMember.user_id == member_id,
                ActivityMember.activity_id == activity_id,
            )
        )).one()

        return Activity.schema.model_validate(activity).model_dump() \
            | {'me': ActivityMember.schema.model_validate(member)}

    activity: Activity = await db_session.scalar_one(
        db.select(Activity).where(Activity.object_id == activity_id)
    )

    return Activiy.schema.model_validate(activity)


@router.get('/{activity_id}/members')
async def api_v1_activities__members__get(
    activity_id: ObjectId = Path(...),
    member_id: Optional[ObjectId] = Query(None),
    page: Annotated[int, Gt(0)] = Query(...),
    query: Optional[str] = Query(None),
    size: Annotated[int, Gt(0), Le(100)] = Query(...),
    db_session: db.AsyncSession = Depends(db.session),
    token: Token = Depends(crypto.token),
):
    def gen_query(q: db.Query) -> db.Query:
        if query: q = q.where(User.name.like(f'%{utils.escape_str(query)}%'))
        return q

    total: int = await db_session.scalar(
        gen_query(db.select(db.func.count()).select_from(ActivityMember).join(User))
    ) or 0

    return {
        'data': [
            ActivityMember.schema.model_validate(x)
            async for x in db_session.stream_scalars(
                gen_query(db.select(ActivityMember).join(User)).limit(size).offset(page * size - size)
            )
        ],
        'metadata': {
            'total': total,
            'last': page * size >= total,
        }
    }