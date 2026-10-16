from fastapi import APIRouter, Depends, HTTPException
from db import backends
import numpy as np

from util.activity_statistics import (
    build_analysis_payload,
    describe_durations,
    empty_analysis_payload,
)
from util.object_id import get_current_user

router = APIRouter()


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

    Members are mapped to their class with two full reads (``list_all`` for the
    users, then the groups repo's ``list_all`` class listing) instead of a
    dedicated batch protocol method: the tables are small, this needs no new
    repository surface, and it keeps the endpoint to two queries — the paginated
    ``list_classes`` would cost a third count query just to learn the total.
    An activity with no effective member answers the empty payload without those
    reads at all. The assembly itself lives in
    :func:`util.activity_statistics.build_analysis_payload`.

    :param activity_id: The ID of the activity
    :param user: Current user, used for authentication
    """
    _require_statistics_reader(user)
    members = await backends.get_members_repo().list_by_activity(
        activity_id, status="effective"
    )
    if not members:
        return empty_analysis_payload()
    users = await backends.get_users_repo().list_all()
    classes = await backends.get_groups_repo().list_all()
    return build_analysis_payload(members, users, classes)


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
    return result["percentiles"][grade]


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
    return result["indicators"][grade]
