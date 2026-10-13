from fastapi import APIRouter, Depends
from db import backends
import numpy as np
from scipy.stats import mode

from util.object_id import get_current_user

router = APIRouter()


@router.get("/{activity_id}/description")
async def activity_issuance_description(activity_id: str, _=Depends(get_current_user)):
    """
    Get the data description of an activity by its ID.
    It consists: mean, median, mode, standard deviation, min, max, and variance.
    :param activity_id: The ID of the activity
    :param _: Current user, used for authentication
    """
    distribution = await backends.get_members_repo().list_by_activity(activity_id)
    if not distribution:
        return {"code": 404, "status": "not found", "data": None}
    distribution = [float(item["duration"]) for item in distribution]
    distribution = np.array(distribution, dtype=np.float64)
    mean = np.mean(distribution).item()
    median = float(np.median(distribution))
    std_dev = np.std(distribution).item()
    min_val = np.min(distribution).item()
    max_val = np.max(distribution).item()
    variance = np.var(distribution).item()
    sum_val = np.sum(distribution).item()
    twenty_fifth = np.percentile(distribution, 25).item()
    seventy_fifth = np.percentile(distribution, 75).item()
    total = len(distribution)
    return {
        "mean": mean,
        "median": median,
        "mode": mode(distribution).mode,
        "std": std_dev,
        "min": min_val,
        "max": max_val,
        "var": variance,
        "sum": sum_val,
        "25_percentile": twenty_fifth,
        "75_percentile": seventy_fifth,
        "total": total,
    }


@router.get("/{activity_id}/layers")
async def activity_issuance_layers(activity_id: str, _=Depends(get_current_user)):
    """
    Get the layers of an activity by its ID.
    :param activity_id: The ID of the activity
    :param _: Current user, used for authentication
    """
    distribution = await backends.get_members_repo().list_by_activity(activity_id)
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


@router.get("/grades/{grade}/percentiles")
async def get_time_percentiles(grade: str, _=Depends(get_current_user)):
    """
    Get the percentiles of time for a specific grade.
    :param grade: The grade to get percentiles for
    :param _: Current user, used for authentication
    """
    result = await backends.get_stats_repo().get_indicators()
    if result is None:
        # If there are no indicators, return an empty dictionary
        return {}
    return result["percentiles"][grade]


@router.get("/grades/{grade}/indicators")
async def get_time_indicators(grade: str, _=Depends(get_current_user)):
    """
    Get the statistical indicators of time for a specific grade.
    :param grade: The grade to get indicators for
    :param _: Current user, used for authentication
    """
    result = await backends.get_stats_repo().get_indicators()
    if result is None:
        # If there are no indicators, return an empty dictionary
        return {}
    return result["indicators"][grade]
