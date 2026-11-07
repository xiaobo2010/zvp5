from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
import logging
from typing import Any, Literal
from config import BASE_ON_CAMPUS, BASE_OFF_CAMPUS, BASE_SOCIAL_PRACTICE
from db import backends
from db.ids import normalize_oid
from settings import LANGUAGE
from typings.time import UserActivityTime
from util.oid import validate_object_id
import pandas as pd

logger = logging.getLogger(__name__)


def describe_memberships(
    collections: Sequence[Mapping[str, Any]],
    activities: Sequence[Mapping[str, Any] | None],
) -> str:
    """Render the ``description`` text for one user's effective memberships.

    ``collections`` and ``activities`` must be **aligned**: ``activities[i]`` is
    the activity resolved for ``collections[i]`` (``None`` when the row is gone).
    Keeping the resolution outside makes the formatting itself pure, so callers
    that already batch-loaded activities pay no per-user round trip while the
    legacy callers keep their old behaviour.

    The output is exactly what the historical per-item loop produced: the same
    ``effective`` filter, the same order, the same separators.
    """
    desc = []
    for collection, activity in zip(collections, activities):
        if not activity or activity.get("status") != "effective":
            continue
        if LANGUAGE == "zh-CN":
            modes = {
                "on-campus": "校内",
                "off-campus": "校外",
                "social-practice": "社会实践",
            }
            desc.append(
                f"{activity['name']}（{activity['date'].strftime('%Y-%m-%d')}），{modes.get(collection['mode'], "未知")} {collection['duration']} 小时"
            )
        else:
            desc.append(
                f"{activity['name']} (at {activity['date'].strftime('%Y-%m-%d')}), {collection['mode']} {collection['duration']} hours"
            )
    if LANGUAGE == "zh-CN":
        description = "；".join(desc)
    elif len(desc) == 0:
        description = "No activities found"
    elif len(desc) == 1:
        description = desc[0]
    elif len(desc) == 2:
        description = " and ".join(desc)
    else:
        description = ", ".join(desc[:-1]) + " and " + desc[-1]
    return description


async def generate_description(
    user_id: str,
    memberships: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    activities: Mapping[str, Mapping[str, Any]] | None = None,
):
    """Description text for one user.

    ``memberships`` / ``activities`` are optional pre-loaded maps
    (``user_oid -> effective membership rows`` and
    ``activity_oid -> activity document``): a caller looping over users passes
    them once instead of re-reading both tables per user. ``None`` keeps the
    historical per-user queries, so old two/one-argument calls behave exactly as
    before.
    """
    if memberships is None:
        collections = await backends.get_members_repo().list_for_member(
            user_id, status="effective"
        )
    else:
        collections = list(memberships.get(str(user_id), ()))
    if activities is None:
        resolved = [
            await backends.get_activities_repo().get(collection["activity"])
            for collection in collections
        ]
    else:
        resolved = [
            activities.get(str(collection["activity"])) for collection in collections
        ]
    return describe_memberships(collections, resolved)


# NOTE: ``calculate_user_time_with_origin`` and ``time_with_origin`` now have no
# caller (the set-based ``tasks/compute_time.rebuild_statistics`` replaced them).
# Kept as part of the read-path/compat surface; the historical per-user write path
# they fed is no longer used by the recompute.
async def calculate_user_time_with_origin(
    user_id: str,
    allow_cache: bool = False,
    load_cache: bool = True
):
    """
    Get user time with origin data

    :param user_id: User ID
    :param allow_cache: If True, do not use cached data
    :param load_cache: If True, load the calculated data into cache

    effective-only: only ``member_status == "effective"`` memberships are counted,
    matching the ``user_time`` recompute so both read paths agree (the original
    implementation did not filter ``status``, which was a defect).

    :return: User time with origin data
    """
    logger.debug("Calculating time for user %s.", user_id)
    cache_data = await backends.get_time_repo().get_with_origin(user_id) if allow_cache else None
    if cache_data:
        return cache_data['data']
    accepted = await backends.get_activities_repo().list_effective()
    accepted_ids = [str(activity["_id"]) for activity in accepted]
    db_data = await backends.get_members_repo().list_for_member(
        user_id, status="effective", activity_oids=accepted_ids
    )
    categorize_by_origin = {} # (certain origin) -> { 'on-campus': float, 'off-campus': float, 'social-practice': float }
    for data in db_data:
        activity = await backends.get_activities_repo().get(data["activity"])
        origin = activity.get('origin', 'unknown')
        if origin not in categorize_by_origin:
            categorize_by_origin[origin] = {
                "on-campus": 0.0,
                "off-campus": 0.0,
                "social-practice": 0.0
            }
        categorize_by_origin[origin][data['mode']] += data['duration']
    # Convert `categorize_by_origin` to DataFrame
    df = pd.DataFrame.from_dict(categorize_by_origin, orient='index').fillna(0)
    # Find percentage of each origin and mode, result format analog to the original, but the data is not raw hours, but percentage
    total = df.sum().sum()
    percentage = df.sum(axis=1) / total * 100 if total > 0 else pd.Series(0, index=df.index)
    result = {
        "by_origin": df.to_dict(orient='index'),
        "percentage_by_origin": percentage.to_dict()
    }
    if load_cache:
        await backends.get_time_repo().upsert_with_origin(str(user_id), result)
    return result


async def time_with_origin(
    scope: Literal["grade", "group"],
    scope_id: str,
    require_percentage: int = 0,
):
    """
    Get certain scope time with origin data

    :param scope: Scope type, either "grade" or "group"
    :param scope_id: Scope ID. For `grade`, it is the four-digit number of entry year; for `group`, it is the ObjectID of group.
    :param require_percentage: If > 0, only count students whose total time percentage is greater than this value
    """
    logger.debug("Calculating time for scope %s with id %s.", scope, scope_id)
    if scope == "grade":
        users = await backends.get_users_repo().list_by_id_prefix(scope_id)
    elif scope == "group":
        users = await backends.get_users_repo().list_by_class(
            validate_object_id(scope_id)
        )
    kept = []
    for user in users:
        await calculate_user_time_with_origin(str(user["_id"]), allow_cache=require_percentage == 0, load_cache=require_percentage == 0)
        if require_percentage > 0:
            user_time = await calculate_user_time(str(user["_id"]), allow_cache=True)
            validated = UserActivityTime(
                on_campus_raw=user_time["on-campus"],
                off_campus_raw=user_time["off-campus"],
                social_practice=user_time["social-practice"],
                updated_at=datetime.now(),
                user=str(user["_id"]),
                _id=''
            )
            if validated.percentage < require_percentage:
                continue
        kept.append(user)
    users = kept
    data = await backends.get_time_repo().list_with_origin(
        [str(user["_id"]) for user in users]
    )
    df_list = []
    for item in data:
        df = pd.DataFrame.from_dict(item['data']['by_origin'], orient='index').fillna(0)
        df_list.append(df)
    logger.debug("%s", df_list)
    if not df_list:
        return {
            "by_origin": {},
            "percentage_by_origin": {}
        }
    combined_df = pd.concat(df_list).groupby(level=0).sum()
    total = combined_df.sum().sum()
    percentage = combined_df.sum(axis=1) / total * 100 if total > 0 else pd.Series(0, index=combined_df.index)
    result = {
        "by_origin": combined_df.to_dict(orient='index'),
        "percentage_by_origin": percentage.to_dict()
    }
    stat_key = f"{scope}_{scope_id}_{require_percentage}"
    await backends.get_time_repo().upsert_with_origin_stat(
        stat_key, scope, scope_id, require_percentage, result
    )
    return result


async def calculate_user_time(
    user_id: str,
    date_start: datetime | None = None,
    date_end: datetime | None = None,
    allow_cache: bool = True,
    attach_description: bool = False,
    activities: list | None = None,
):
    """
    Get user time

    :param user_id: User ID
    :param date_start: Start date for filtering activities
    :param date_end: End date for filtering activities
    :param allow_cache: If True, do not use cached data
    :param attach_description: If True, attach description to the result
    :param activities: Pre-resolved ``list_effective`` rows for this date range.
        Callers looping over users pass the same set once instead of re-reading
        the whole activity table per user; ``None`` keeps the historical query.

    :return: User time
    """
    logger.debug(
        "Calculating time for user %s with date range %s to %s, allow_cache=%s, attach_description=%s",
        user_id,
        date_start,
        date_end,
        allow_cache,
        attach_description,
    )
    if allow_cache and date_start is None and date_end is None:
        db_data = await backends.get_time_repo().get_for_user(user_id)
        if db_data:
            return {
                "on-campus": db_data["on_campus_raw"],
                "off-campus": db_data["off_campus_raw"],
                "social-practice": db_data["social_practice"],
            }

    if activities is None:
        activities = await backends.get_activities_repo().list_effective(
            date_start, date_end
        )
    accepted_activities = [str(activity["_id"]) for activity in activities]

    collections = await backends.get_members_repo().list_for_member(
        user_id,
        status="effective",
        activity_oids=accepted_activities,
    )

    result = defaultdict(float)
    result["on-campus"] = 0
    result["off-campus"] = 0
    result["social-practice"] = 0
    for m in collections:
        result[m["mode"]] += m["duration"]
    result = dict(result)
    if attach_description:
        description = await generate_description(user_id)
        result["description"] = description

    if not allow_cache and date_start is None and date_end is None:
        await backends.get_time_repo().upsert_for_user(
            user_id,
            result["on-campus"],
            result["off-campus"],
            result["social-practice"],
        )

    return result


async def calculate_user_time_bulk(
    user_ids: list[str],
    date_start: datetime | None = None,
    date_end: datetime | None = None,
    allow_cache: bool = True,
    session=None,
):
    """Bulk user times.

    ``session`` is an optional caller-owned ``AsyncSession``: when supplied,
    every repository call below runs on it (one checkout / one teardown per
    request instead of one per statement). When omitted the repos keep opening
    their own sessions, i.e. the historical behaviour is unchanged.
    """
    ids = [str(user_id) for user_id in user_ids]
    results: dict[str, dict] = {}
    pending: list[str] = []

    if allow_cache and date_start is None and date_end is None:
        cached = await backends.get_time_repo(session).list_by_users(ids)
        cached_index = {normalize_oid(row["user"]): row for row in cached}
        for user_id in ids:
            row = cached_index.get(normalize_oid(user_id))
            if row is None:
                pending.append(user_id)
            else:
                results[user_id] = {
                    "on-campus": row["on_campus_raw"],
                    "off-campus": row["off_campus_raw"],
                    "social-practice": row["social_practice"],
                }
    else:
        pending = list(ids)

    if pending:
        accepted_activities = await backends.get_activities_repo(session).list_effective(
            date_start, date_end
        )
        accepted_set = {
            normalize_oid(str(activity["_id"])) for activity in accepted_activities
        }

        collections = await backends.get_members_repo(session).list_by_members(pending)

        aggregates: dict[str, dict] = {}
        for user_id in pending:
            bucket = defaultdict(float)
            bucket["on-campus"] = 0
            bucket["off-campus"] = 0
            bucket["social-practice"] = 0
            aggregates[normalize_oid(user_id)] = bucket

        for collection in collections:
            if collection["status"] != "effective":
                continue
            if normalize_oid(str(collection["activity"])) not in accepted_set:
                continue
            bucket = aggregates.get(normalize_oid(collection["member"]))
            if bucket is None:
                continue
            bucket[collection["mode"]] += collection["duration"]

        for user_id in pending:
            results[user_id] = dict(aggregates[normalize_oid(user_id)])

    return results


def find_percentile_threshold(
    percentiles: dict[str, float], target: float, mode: str
) -> float:
    thresholds = {
        "on-campus": BASE_ON_CAMPUS,
        "off-campus": BASE_OFF_CAMPUS,
        "social-practice": BASE_SOCIAL_PRACTICE,
    }
    if target >= thresholds[mode]:
        return 100
    percentiles = sorted(percentiles.items(), key=lambda item: item[1], reverse=True)
    for key, value in percentiles:
        if target >= value:
            return int(key.replace("%", ""))
    return 100
