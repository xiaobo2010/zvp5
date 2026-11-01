from io import BytesIO
from fastapi import Response
from fastapi.responses import StreamingResponse
import pandas as pd
from tqdm import tqdm

from util.calculate import calculate_user_time
from util.get_class import get_classname


def json2dataframe(data: list[dict]):
    result = {}
    for item in data:
        result[item["id"]] = [
            item["info"]["name"],
            item["info"]["id"],
            item["info"]["group"],
            item["time"]["on-campus"],
            item["time"]["off-campus"],
            item["time"]["social-practice"],
            item["time"]["trophy"],
            item["time"]["total"],
        ]
    df = pd.DataFrame(result)
    return df


def json2csv(data: list[dict]):
    df = json2dataframe(data)
    buffer = df.to_csv(index=False)
    return Response(
        content=buffer,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=time.csv"},
    )


def json2xlsx(data: list[dict]):
    df = json2dataframe(data)
    # 直接写内存缓冲：NamedTemporaryFile(delete=False) 每次下载都会在 /tmp
    # 留下一个 xlsx，异常路径连清理机会都没有。
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        content=buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=time.xlsx"},
    )


async def calculate(
    users: list[dict],
    normal_activities: list[dict],
    special_activities: list[dict],
    prize_activities: list[dict],
    trophies: list[dict],
    prize_full: float,
    discount: bool,
    groups: list[dict],
):
    result = []
    for user in tqdm(users):
        time = await calculate_user_time(
            str(user["_id"]),
        )
        classname = await get_classname(user, groups)
        if classname is None:
            continue
        result.append(
            {
                "id": str(user["_id"]),
                "time": time,
                "info": {"name": user["name"], "id": user["id"], "group": classname},
            }
        )
    return result
