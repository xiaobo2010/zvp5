import os
import re
import logging
from collections import defaultdict
from typing import Any, cast
import tarfile
import numpy as np
import pandas as pd
from datetime import datetime
from config import (
    BASE_OFF_CAMPUS,
    OFF_TO_ON_RATE,
    MAX_EXCEED_DISCOUNT,
    BASE_ON_CAMPUS,
    ON_TO_OFF_RATE,
    BASE_SOCIAL_PRACTICE,
)
from db import backends
from db.oracle.session import session_scope
from util.statement import (
    build_statement_data,
    write_statement_json,
    TRANSLATIONS,
)

_logger = logging.getLogger(__name__)


async def generate_reports():
    classes = await backends.get_groups_repo().list_non_permission()
    await backends.get_daily_exports_repo().delete_all()
    db_id = await backends.get_daily_exports_repo().insert(
        {"created_at": datetime.now(), "status": "processing", "data": []}
    )
    try:
        if not os.path.exists("./export"):
            os.makedirs("./export")
        else:
            for item in os.listdir("./export"):
                item_path = os.path.join("./export", item)
                if os.path.isdir(item_path):
                    for file in os.listdir(item_path):
                        file_path = os.path.join(item_path, file)
                        if file.endswith((".pdf", ".json")):
                            os.remove(file_path)
                    os.rmdir(item_path)
        for class_id in classes:
            users = await backends.get_users_repo().list_by_class(class_id["_id"])
            class_dir = f'./export/{class_id["name"]}'
            os.makedirs(class_dir, exist_ok=True)
            for user in users:
                user_id = str(user["id"])
                entry = int(user_id[:4])
                soy = datetime.now().replace(
                    month=8, day=1, year=entry, hour=0, minute=0, second=0, microsecond=0
                )
                eoy = datetime.now().replace(
                    month=7,
                    day=31,
                    year=entry + 3,
                    hour=23,
                    minute=59,
                    second=59,
                    microsecond=999999,
                )
                os.makedirs(class_dir, exist_ok=True)
                filename = f'{class_dir}/{user["id"]}_{user["name"]}.json'
                volunteer_data, spreadsheet = await build_statement_data(
                    str(user["_id"]),
                    (soy, eoy),
                    language="zh",
                )
                write_statement_json(volunteer_data, spreadsheet, filename)
                await backends.get_daily_exports_repo().push_data(
                    db_id,
                    {
                        **spreadsheet,
                        "filename": filename,
                    },
                )
                print(
                    f"Exported statement for {user['name']} in class {class_id['name']} (saved to {filename})."
                )
            print(f"Exported statements for class {class_id['name']}.")
        # Create a tar.gz archive of the export directory
        spreadsheet_items = await backends.get_daily_exports_repo().find_one(db_id)
        spreadsheet_items = spreadsheet_items.get("data", [])
        df = pd.DataFrame(spreadsheet_items)
        df.rename(columns=TRANSLATIONS["zh"]["spreadsheet"], inplace=True)
        if os.path.exists("./export/summary.xlsx"):
            os.remove("./export/summary.xlsx")
        df.to_excel(f"./export/summary.xlsx", index=False)
        os.makedirs("./data", exist_ok=True)
        if os.path.exists("./data/export.tar.gz"):
            os.remove("./data/export.tar.gz")
        with tarfile.open("./data/export.tar.gz", "w:gz") as tar:
            tar.add("./export", arcname="export")
        return "./data/export.tar.gz"
    except Exception:
        _logger.exception("Failed to generate daily reports")
        try:
            await backends.get_daily_exports_repo().update_fields(
                db_id, {"status": "failed"}
            )
        except Exception:
            _logger.exception("Failed to mark the daily export row as failed")
        raise


def describe_percentile(items: np.ndarray, step: int, bound: float) -> dict[str, float]:
    """
    Calculate percentiles for a given array of items.

    :param items: A numpy array of numerical values.
    :param step: The step size for calculating percentiles.
    :param bound: A bound value to limit the maximum percentile value.
    :return: A dictionary containing the percentiles.
    """
    percentiles = {}
    for i in range(100, -1, -step):
        percentiles[f"{i}%"] = round(min(np.percentile(items, i).item(), bound), 1)
    # Deduplicate. If the value is the same, only the smallest percentile key is
    # kept (we iterate from 100% down, so later/smaller keys overwrite earlier ones).
    dictionary = defaultdict(float)
    for key, value in percentiles.items():
        dictionary[value] = key
    result = cast(dict[str, float], {v: k for k, v in dictionary.items()})
    return result


def describe_statistical_indicators(items: np.ndarray) -> dict[str, float]:
    """
    Calculate statistical indicators for a given array of items.

    :param items: A numpy array of numerical values.
    :return: A dictionary containing the statistical indicators.
    """
    return {
        "mean": np.mean(items).item(),
        "median": float(np.median(items)),
        "mode": float(pd.Series(items).mode().iloc[0]),
        "std": np.std(items).item(),
        "min": np.min(items).item(),
        "max": np.max(items).item(),
        "var": np.var(items).item(),
        "sum": np.sum(items).item(),
    }


def process_time_data(time_df: pd.DataFrame) -> pd.DataFrame:
    """
    Process the time data DataFrame to calculate on-campus, off-campus, and social practice time.

    :param time_df: A DataFrame containing time data with columns 'on_campus_raw', 'off_campus_raw', and 'social_practice'.
    :return: A processed DataFrame with additional columns for on-campus, off-campus, and social practice time.
    """
    time_df.fillna(0, inplace=True)
    time_df["on-campus"] = time_df["on_campus_raw"] + np.round(
        (
            (time_df["off_campus_raw"] - BASE_OFF_CAMPUS).clip(lower=0) * OFF_TO_ON_RATE
        ).clip(lower=0, upper=MAX_EXCEED_DISCOUNT),
        1,
    )
    time_df["off-campus"] = time_df["off_campus_raw"] + np.round(
        (
            (time_df["on_campus_raw"] - BASE_ON_CAMPUS).clip(lower=0) * ON_TO_OFF_RATE
        ).clip(lower=0, upper=MAX_EXCEED_DISCOUNT),
        1,
    )
    time_df["social-practice"] = time_df["social_practice"]
    time_df["diff"] = (
        BASE_ON_CAMPUS
        + BASE_OFF_CAMPUS
        + BASE_SOCIAL_PRACTICE
        - time_df["on-campus"]
        - time_df["off-campus"]
        - time_df["social-practice"]
    )
    return time_df


def describe_df_percentile(
    df: pd.DataFrame, columns: list[str], step: int, bounds: list[float]
) -> dict[str, dict[str, float]]:
    """
    Calculate percentiles for a specific column in a DataFrame.

    :param df: A pandas DataFrame containing the data.
    :param columns: The column names for which to calculate percentiles.
    :param step: The step size for calculating percentiles.
    :param bounds: Bound values to limit the maximum percentile value.
    :return: A dictionary containing the percentiles for the specified column.
    """
    result = {}
    for column, bound in zip(columns, bounds):
        if column not in df.columns:
            raise ValueError(f"Column '{column}' does not exist in the DataFrame.")
        items = df[column].to_numpy()
        percentiles = describe_percentile(items, step, bound)
        result[column] = percentiles
    return result


def describe_df_statistical_indicators(
    df: pd.DataFrame, columns: list[str]
) -> dict[str, dict[str, float]]:
    """
    Calculate statistical indicators for specific columns in a DataFrame.

    :param df: A pandas DataFrame containing the data.
    :param columns: The column names for which to calculate statistical indicators.
    :return: A dictionary containing the statistical indicators for the specified columns.
    """
    result = {}
    for column in columns:
        if column not in df.columns:
            raise ValueError(f"Column '{column}' does not exist in the DataFrame.")
        items = df[column].to_numpy()
        indicators = describe_statistical_indicators(items)
        result[column] = indicators
    return result


_MODES = ("on-campus", "off-campus", "social-practice")
_BASE_TOTAL = BASE_ON_CAMPUS + BASE_OFF_CAMPUS + BASE_SOCIAL_PRACTICE
_REQUIRE_PERCENTAGES = (0, 20, 60, 80)


def _adjusted_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Fold raw hours into the on/off/social columns and add ``percentage``.

    Reuses :func:`process_time_data` (the ``np.round(..., 1)`` on the transferred
    hours stays in Python on purpose, so it never diverges from Oracle's
    half-away-from-zero ``ROUND``).

    ``percentage`` mirrors ``UserActivityTime.percentage`` (``typings/time.py``):
    it folds from the *raw* columns (no ``np.round``) and clamps the shortfall
    per mode, so a surplus in one mode never offsets a deficit in another and
    the result cannot exceed 100%. This is the exact filter the old
    ``time_with_origin`` applied via ``require_percentage``.
    """
    columns = [
        "user_oid",
        "grade",
        "class_oid",
        "on-campus",
        "off-campus",
        "social-practice",
        "percentage",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    # ``process_time_data`` fills NaN in place, which would coerce a NULL
    # ``class_oid`` to 0; keep the original so class-less users stay NULL.
    class_oid = df["class_oid"].copy()
    process_time_data(df)
    df["class_oid"] = class_oid
    # Folded hours as deployed, but recomputed from the raw columns without
    # ``np.round`` so ``percentage`` matches ``UserActivityTime`` exactly.
    folded_on = df["on_campus_raw"] + (
        (df["off_campus_raw"] - BASE_OFF_CAMPUS).clip(lower=0) * OFF_TO_ON_RATE
    ).clip(lower=0, upper=MAX_EXCEED_DISCOUNT)
    folded_off = df["off_campus_raw"] + (
        (df["on_campus_raw"] - BASE_ON_CAMPUS).clip(lower=0) * ON_TO_OFF_RATE
    ).clip(lower=0, upper=MAX_EXCEED_DISCOUNT)
    deficit = (
        (BASE_ON_CAMPUS - folded_on).clip(lower=0)
        + (BASE_OFF_CAMPUS - folded_off).clip(lower=0)
        + (BASE_SOCIAL_PRACTICE - df["social_practice"]).clip(lower=0)
    )
    df["percentage"] = (_BASE_TOTAL - deficit) / _BASE_TOTAL * 100
    return df


def _grade_rollups(adjusted: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    """Per-grade percentiles/indicators keyed by grade (empty groups skipped)."""
    percentiles: dict[str, Any] = {}
    indicators: dict[str, Any] = {}
    if adjusted is None or adjusted.empty:
        return percentiles, indicators
    for grade, group in adjusted.groupby("grade"):
        if group.empty:
            continue
        percentiles[str(grade)] = describe_df_percentile(
            group,
            list(_MODES),
            step=1,
            bounds=[BASE_ON_CAMPUS, BASE_OFF_CAMPUS, BASE_SOCIAL_PRACTICE],
        )
        indicators[str(grade)] = describe_df_statistical_indicators(group, list(_MODES))
    return percentiles, indicators


def _group_rollup_rows(adjusted: pd.DataFrame) -> list[dict[str, Any]]:
    """Per-class percentiles/indicators rows (users without a class produce none)."""
    rows: list[dict[str, Any]] = []
    if adjusted is None or adjusted.empty:
        return rows
    for class_oid, group in adjusted.groupby("class_oid"):
        if class_oid is None or (isinstance(class_oid, float) and pd.isna(class_oid)):
            continue
        if group.empty:
            continue
        rows.append(
            {
                "group_oid": str(class_oid),
                "percentiles": describe_df_percentile(
                    group,
                    list(_MODES),
                    step=1,
                    bounds=[BASE_ON_CAMPUS, BASE_OFF_CAMPUS, BASE_SOCIAL_PRACTICE],
                ),
                "indicators": describe_df_statistical_indicators(group, list(_MODES)),
            }
        )
    return rows


def _per_user_origins(origins: list[Any]) -> dict[str, dict[str, dict[str, float]]]:
    """``user_oid -> {origin: {mode: hours}}`` with all three modes initialised."""
    per_user: dict[str, dict[str, dict[str, float]]] = {}
    for row in origins:
        user = str(row["user_oid"])
        by_origin = per_user.setdefault(user, {})
        bucket = by_origin.setdefault(row["origin"], {mode: 0.0 for mode in _MODES})
        if row["mode"] in bucket:
            bucket[row["mode"]] += float(row["total"])
    return per_user


def _sum_origins(
    users: list[str], per_user: dict[str, dict[str, dict[str, float]]]
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Sum several users' per-origin breakdowns and recompute the percentage map."""
    by_origin: dict[str, dict[str, float]] = {}
    for user in users:
        for origin, bucket in per_user.get(user, {}).items():
            target = by_origin.setdefault(origin, {mode: 0.0 for mode in _MODES})
            for mode in _MODES:
                target[mode] += bucket.get(mode, 0.0)
    total = sum(sum(bucket.values()) for bucket in by_origin.values())
    if total > 0:
        percentage = {
            origin: sum(bucket.values()) / total * 100
            for origin, bucket in by_origin.items()
        }
    else:
        percentage = {}
    return by_origin, percentage


def _origin_user_rows(totals: list[Any], origins: list[Any]) -> list[dict[str, Any]]:
    """One ``time_with_origin`` row per user (zero/origin-less users included)."""
    per_user = _per_user_origins(origins)
    rows: list[dict[str, Any]] = []
    for total in totals:
        user = str(total["user_oid"])
        by_origin, percentage = _sum_origins([user], per_user)
        rows.append(
            {
                "user_oid": user,
                "data": {"by_origin": by_origin, "percentage_by_origin": percentage},
            }
        )
    return rows


def _origin_stat_rows(
    totals: list[Any], origins: list[Any], adjusted: pd.DataFrame
) -> list[dict[str, Any]]:
    """One ``time_with_origin_stat`` row per scope × ``require_percentage``.

    Grade scope is keyed by the 4-digit grade; group scope by ``class_oid`` (the
    groups.oid the caller passes through). ``require_percentage > 0`` keeps only
    users whose folded ``percentage`` meets the threshold.
    """
    per_user = _per_user_origins(origins)
    percentages: dict[str, float] = {}
    if adjusted is not None and not adjusted.empty:
        percentages = {
            str(u): float(p)
            for u, p in zip(adjusted["user_oid"], adjusted["percentage"])
        }
    grades: dict[str, list[str]] = defaultdict(list)
    groups: dict[str, list[str]] = defaultdict(list)
    for total in totals:
        user = str(total["user_oid"])
        grades[str(total["grade"])].append(user)
        if total["class_oid"]:
            groups[str(total["class_oid"])].append(user)

    rows: list[dict[str, Any]] = []
    for rq in _REQUIRE_PERCENTAGES:
        for scope, scope_map in (("grade", grades), ("group", groups)):
            for scope_id, users in scope_map.items():
                selected = (
                    users
                    if rq == 0
                    else [u for u in users if percentages.get(u, 0.0) >= rq]
                )
                by_origin, percentage = _sum_origins(selected, per_user)
                rows.append(
                    {
                        "stat_key": f"{scope}_{scope_id}_{rq}",
                        "scope": scope,
                        "scope_id": scope_id,
                        "require_percentage": rq,
                        "data": {
                            "by_origin": by_origin,
                            "percentage_by_origin": percentage,
                        },
                    }
                )
    return rows


async def rebuild_statistics() -> dict[str, int]:
    """Rebuild every derived volunteer-time table in a single transaction.

    SQL does the aggregation (``rebuild_*`` / ``aggregate_*`` on the repos),
    Python does row assembly and JSON serialisation, and there is exactly one
    ``commit`` here (any failure rolls the whole unit of work back).
    """
    now = datetime.now()
    async with session_scope() as session:
        try:
            time_repo = backends.get_time_repo(session)
            stats_repo = backends.get_stats_repo(session)
            counts: dict[str, int] = {}
            counts["user_time"] = await time_repo.rebuild_user_time(now)
            counts["time_academic_year"] = await time_repo.rebuild_academic_years(now)
            totals = await time_repo.aggregate_totals_by_user()
            origins = await time_repo.aggregate_time_by_origin()
            adjusted = _adjusted_frame(pd.DataFrame(totals))
            pct, ind = _grade_rollups(adjusted)
            await stats_repo.rebuild_indicators(pct, ind, now)
            counts["group_indicators"] = await stats_repo.rebuild_group_indicators(
                _group_rollup_rows(adjusted), now
            )
            counts["time_with_origin"] = await time_repo.rebuild_with_origin(
                _origin_user_rows(totals, origins), now
            )
            counts["time_with_origin_stat"] = await time_repo.rebuild_with_origin_stat(
                _origin_stat_rows(totals, origins, adjusted), now
            )
            await session.commit()
            print(f"rebuild_statistics done: {counts}")
            return counts
        except BaseException:
            await session.rollback()
            raise
