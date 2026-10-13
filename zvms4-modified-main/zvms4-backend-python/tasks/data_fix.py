from db import backends
from util.title_modify import modify_title_automatically


async def regulate_titles():
    activities = await backends.get_activities_repo().list_raw()
    for activity in activities:
        new_title = modify_title_automatically(activity["name"])
        await backends.get_activities_repo().update(
            activity["_id"], {"name": new_title}
        )


async def remove_activities_with_zero_participants():
    """
    Remove activities that have no participants.
    """
    activities = await backends.get_activities_repo().list_raw()
    for activity in activities:
        count = await backends.get_members_repo().count_for_activity(
            str(activity["_id"]), status="effective"
        )
        if count == 0:
            await backends.get_activities_repo().delete(activity["_id"])

    refused = await backends.get_activities_repo().list_raw(status="refused")

    for activity in refused:
        await backends.get_members_repo().remove_for_activity(str(activity["_id"]))
        await backends.get_activities_repo().delete(activity["_id"])

    tests, _ = await backends.get_activities_repo().list_raw(
        search="(调试|测试|Test|Debug)"
    )

    for activity in tests:
        await backends.get_members_repo().remove_for_activity(str(activity["_id"]))
        await backends.get_activities_repo().delete(activity["_id"])


async def wash_data():
    """
    Run all data washing tasks.
    """
    # await regulate_titles()
    # await remove_activities_with_zero_participants()
    # Add more data washing tasks here as needed
    # print("Data washing completed.")
