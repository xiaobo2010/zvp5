import re
from datetime import datetime
from typing import Optional

from config import MAX_ACTIVITY_DESCRIPTION_LENGTH

# `activities` 表三列的列宽（`db/oracle/ddl/V001__initial_schema.sql:116-125`，
# 单位 CHAR）：写超了 Oracle 抛 ORA-12899，到 handler 就是 500。列宽只在仓库
# 里写一份，校验与建表同源——改列宽时这里必须一起改。
ACTIVITY_NAME_MAX_LENGTH = 256
ACTIVITY_PLACE_MAX_LENGTH = 256
ACTIVITY_APPROVER_MAX_LENGTH = 24

# `groups` 表两列的列宽（`db/oracle/ddl/V001__initial_schema.sql:91-100`，单位
# CHAR，模型见 `db/models.py:172,175`）：与活动同理，写超了是 ORA-12899 → 500。
GROUP_NAME_MAX_LENGTH = 128
GROUP_DESCRIPTION_MAX_LENGTH = 1000


def _validate_varchar(value: Optional[str], limit: int, label: str):
    """定长文本列的通用校验：NULL 放行、NUL 拒绝、超宽拒绝（不静默截断）。

    NUL 单独拦的原因不是长度——Oracle 会把字符串里的 ``\\x00`` 当终止符吞掉，
    落库值与用户提交的不一致；这种「悄悄改数据」比报错更难查。
    """
    if value is None:
        return True, ""

    if "\x00" in value:
        return False, f"{label} should not contain NUL characters"

    if len(value) > limit:
        return False, f"{label} should be at most {limit} characters"

    return True, ""


def _description_max_length() -> int:
    """活动描述长度上限：运行期设置 ``ops.activity_description_max_length`` 优先。"""
    from services import settings_runtime

    override = settings_runtime.get_override("ops.activity_description_max_length")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return MAX_ACTIVITY_DESCRIPTION_LENGTH


def validate_activity_name(name: str):
    if not name.strip():
        return False, "Should not be empty"

    if name[0] == " " or name[-1] == " ":
        return False, "Should not have space before or after the string"

    # 列宽校验放最后：先给「空/首尾空格」这种更好改的提示，再报长度。
    return _validate_varchar(name, ACTIVITY_NAME_MAX_LENGTH, "Name")


def validate_activity_place(place: Optional[str]):
    """活动地点（``ACTIVITIES.PLACE``，``VARCHAR2(256 CHAR)``，可空）。"""
    return _validate_varchar(place, ACTIVITY_PLACE_MAX_LENGTH, "Place")


def validate_activity_approver(approver: Optional[str]):
    """审核人（``ACTIVITIES.APPROVER``，``VARCHAR2(24 CHAR)``）。

    列宽只有 24：它存的不是用户 oid（24 位十六进制刚好卡满），而是
    ``''`` / ``authority`` / ``member`` 这类哨兵值或 oid 本身。
    """
    return _validate_varchar(
        approver, ACTIVITY_APPROVER_MAX_LENGTH, "Approver"
    )


def validate_activity_description(description: Optional[str]):
    """
    Validate an activity description against the ACTIVITIES.DESCRIPTION column
    (``VARCHAR2(1000 CHAR)``).  ``None``/empty are allowed (the column is
    nullable); anything longer than the limit is rejected so the write cannot
    hit ORA-12899 and surface as a 500.
    """
    if description is None:
        return True, ""

    limit = _description_max_length()
    if len(description) > limit:
        return (
            False,
            f"Should be at most {limit} characters",
        )

    return True, ""


def validate_group_name(name: Optional[str]):
    """班级/用户组名（``GROUPS.NAME``，``VARCHAR2(128 CHAR)``，非空列）。"""
    return _validate_varchar(name, GROUP_NAME_MAX_LENGTH, "Name")


def validate_group_description(description: Optional[str]):
    """班级/用户组描述（``GROUPS.DESCRIPTION``，``VARCHAR2(1000 CHAR)``，可空）。"""
    return _validate_varchar(
        description, GROUP_DESCRIPTION_MAX_LENGTH, "Description"
    )


def validate_student_name(name: str):
    """
    Validate student name.
    1. If the name is in Latin characters, it should be capitalized, and separated by space.
    2. If the name is CJK name, it should be in the correct format.
    3. Should not be empty.
    """
    if not name.strip():
        return False, "Should not be empty"

    if name[0] == " " or name[-1] == " ":
        return False, "Should not have space before or after the string"

    # If the name is Chinese name, it should be in the correct format, at most 5 characters and at least 2 characters.
    cjk = re.match(r"^[\u4e00-\u9fa5]{2,5}$", name)

    if not cjk:
        return False, "Should be Chinese name"

    return True, ""


def validate_number(number: str):
    """
    Validate number.
    1. Should be eight digits.
    2. The first four digits reflects the year of registration, which should be in the range grade 1–3, separating school year by Aug 1st.
    3. Then the two digits indicates the class ID, no more than 30.
    4. The last two digits indicates the student ID, no more than 80.
    5. Should not duplicate with existing student number.
    """
    if not re.match(r"^\d{8}$", number):
        return False, "Should be eight digits"

    year = int(number[:4])
    class_id = int(number[4:6])
    student_id = int(number[6:8])

    if datetime.now().month < 8:
        soy = datetime.now().year - 3
        eoy = datetime.now().year - 1
    else:
        soy = datetime.now().year - 2
        eoy = datetime.now().year

    if year < soy or year > eoy:
        return (
            False,
            "The first four digits reflects the year of registration, which should be in the range grade 1–3, separating school year by Aug 1st",
        )

    if class_id < 1 or class_id > 25:
        return False, "Then the two digits indicates the class ID, no more than 25"

    if student_id < 1 or student_id > 60:
        return False, "The last two digits indicates the student ID, no more than 60"

    return True, ""
