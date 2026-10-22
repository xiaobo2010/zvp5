from fastapi import APIRouter, Depends, HTTPException
from db import backends
from db.oracle.session import session_scope
import asyncio
import numpy as np

from util.activity_statistics import (
    MODE_ORDER,
    build_analysis_payload,
    describe_durations,
    empty_analysis_payload,
)
from util.object_id import get_current_user

router = APIRouter()


def _mode_members(members: list[dict], users: dict[str, dict]) -> dict[str, list[dict]]:
    """Roster per participation mode, assembled from rows already in memory.

    ``users`` is the ``get_many_by_oids`` map the handler just read, so this adds
    no query. A membership whose profile is unknown is skipped rather than
    invented. Modes that carry no entry are omitted; the handler adds the field
    to every non-empty payload regardless, so the response shape never depends
    on the data it carries.
    """
    by_mode: dict[str, list[dict]] = {}
    for member in members:
        profile = users.get(str(member["member"]))
        if profile is None:
            continue
        name = profile.get("name")
        business_id = profile.get("id")
        # Explicit projection: the repository row also carries the password
        # hash and the session id, which must never leave this boundary.
        by_mode.setdefault(member["mode"], []).append(
            {
                "_id": str(profile["_id"]),
                "id": str(business_id) if business_id else "",
                "name": name or "",
                "duration": float(member["duration"]),
            }
        )
    order = {mode: index for index, mode in enumerate(MODE_ORDER)}
    return {
        mode: by_mode[mode]
        for mode in sorted(by_mode, key=lambda mode: (order.get(mode, len(order)), mode))
    }


def _require_statistics_reader(user: dict) -> None:
    """Statistics are readable by 团支书及以上的角色 only."""
    if not ({"admin", "department", "secretary"} & set(user.get("per") or [])):
        raise HTTPException(status_code=403, detail="权限不足")


@router.get("/{activity_id}/description")
async def activity_issuance_description(
    activity_id: str, user=Depends(get_current_user)
):
    """
    Get the data description of an activity by its ID.
    It consists: mean, median, mode, standard deviation, min, max, and variance.
    Only memberships with status ``effective`` are counted, and the maths come
    from :func:`util.activity_statistics.describe_durations`.
    :param activity_id: The ID of the activity
    :param user: Current user, used for authentication
    """
    _require_statistics_reader(user)
    distribution = await backends.get_members_repo().list_by_activity(
        activity_id, status="effective"
    )
    if not distribution:
        return {"code": 404, "status": "not found", "data": None}
    distribution = [float(item["duration"]) for item in distribution]
    return describe_durations(distribution)


@router.get("/{activity_id}/layers")
async def activity_issuance_layers(activity_id: str, user=Depends(get_current_user)):
    """
    Get the layers of an activity by its ID.
    Only memberships with status ``effective`` are counted.
    :param activity_id: The ID of the activity
    :param user: Current user, used for authentication
    """
    _require_statistics_reader(user)
    distribution = await backends.get_members_repo().list_by_activity(
        activity_id, status="effective"
    )
    if not distribution:
        return {"code": 404, "status": "not found", "data": None}
    distribution = [item["duration"] for item in distribution]
    distribution = np.array(distribution, dtype=np.float32)

    # Then we find different values, which indicates the layers
    unique_values = np.unique(distribution)
    layers = [
        {"value": float(value), "count": int(np.sum(distribution == value))}
        for value in unique_values
    ]
    layers.sort(key=lambda x: x["count"])
    return layers[::-1]


@router.get("/{activity_id}/analysis")
async def activity_issuance_analysis(activity_id: str, user=Depends(get_current_user)):
    """
    Get the histogram, grouped and descriptive statistics of an activity by its ID.
    Only memberships with status ``effective`` are counted: the same population
    as the time rebuild uses, so the numbers agree with the rest of the system.
    The payload is a superset of the description/layers endpoints, which are kept
    as fallbacks.

    Members are mapped to their class with a single users read restricted to the
    oids the memberships actually reference (plus the groups repo's ``list_all``
    class listing): the tables are small, this needs no new repository surface,
    and it keeps the endpoint to two queries — the paginated ``list_classes``
    would cost a third count query just to learn the total.
    An activity with no effective member answers the empty payload without those
    reads at all. The assembly itself lives in
    :func:`util.activity_statistics.build_analysis_payload`.

    :param activity_id: The ID of the activity
    :param user: Current user, used for authentication
    """
    _require_statistics_reader(user)
    # The members read gates everything below: an activity with no effective
    # member still answers the empty payload without touching users/groups.
    # Once the population is known, the two remaining reads are independent of
    # each other and run concurrently — the class listing on its own session
    # (a session cannot run two statements at once), the member profiles on the
    # session that already read the memberships.
    async with session_scope() as session:
        members = await backends.get_members_repo(session).list_by_activity(
            activity_id, status="effective"
        )
        if not members:
            return empty_analysis_payload()
        users, classes = await asyncio.gather(
            backends.get_users_repo(session).get_many_by_oids(
                [member["member"] for member in members]
            ),
            _all_classes(),
        )
    payload = build_analysis_payload(members, list(users.values()), classes)
    # Additive field, assembled from the memberships and profiles read above: no
    # extra query, and the six historical keys are untouched. Emitted on every
    # non-empty payload so the shape never depends on the data — the empty
    # activity keeps `empty_analysis_payload()` exactly as before.
    payload["mode_members"] = _mode_members(members, users)
    return payload


async def _all_classes() -> list[dict]:
    """``groups.list_all`` on its own session, for the concurrent read above."""
    async with session_scope() as session:
        return await backends.get_groups_repo(session).list_all()


def _grade_series(result: dict, key: str, grade: str):
    series = result.get(key)
    if not isinstance(series, dict) or grade not in series:
        raise HTTPException(status_code=404, detail="该年级暂无统计数据")
    return series[grade]


@router.get("/grades/{grade}/percentiles")
async def get_time_percentiles(grade: str, user=Depends(get_current_user)):
    """
    Get the percentiles of time for a specific grade.
    :param grade: The grade to get percentiles for
    :param user: Current user, used for authentication
    """
    _require_statistics_reader(user)
    result = await backends.get_stats_repo().get_indicators()
    if result is None:
        # If there are no indicators, return an empty dictionary
        return {}
    return _grade_series(result, "percentiles", grade)


@router.get("/grades/{grade}/indicators")
async def get_time_indicators(grade: str, user=Depends(get_current_user)):
    """
    Get the statistical indicators of time for a specific grade.
    :param grade: The grade to get indicators for
    :param user: Current user, used for authentication
    """
    _require_statistics_reader(user)
    result = await backends.get_stats_repo().get_indicators()
    if result is None:
        # If there are no indicators, return an empty dictionary
        return {}
    return _grade_series(result, "indicators", grade)
