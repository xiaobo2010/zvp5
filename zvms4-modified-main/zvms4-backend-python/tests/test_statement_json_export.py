"""``util/statement.py`` 每日导出 JSON 序列化的离线单测。

只覆盖纯函数 ``statement_data_to_json``：不触库、不渲染 PDF、不写盘
（写文件在调用方走 ``write_statement_json``）。钉住的是每日导出从 PDF 切到
JSON 后的顶层契约：七个顶层 key、三个 stats 块都带上 ``total_hours``
（pydantic ``@property`` 不会自动进 ``model_dump()``）、明细字段完整，以及
``ensure_ascii=False``（中文原样落盘，不转义）。

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_statement_json_export.py -q
"""

from __future__ import annotations

import json

from util.statement import (
    StatsData,
    StatementRecord,
    UserData,
    VolunteerStatementData,
    statement_data_to_json,
)


def _sample() -> tuple[VolunteerStatementData, dict]:
    data = VolunteerStatementData(
        user_data=UserData(
            name="张三",
            student_id="20240001",
            class_name="高一(1)班",
            database_id="65f0000000000000000000ab",
            export_time="2026-10-19 12:00:00",
            export_period="2024年08月01日——2027年07月31日",
        ),
        stats_data=StatsData(
            on_campus_hours=1.5, off_campus_hours=2.0, social_practice_hours=3.0
        ),
        stats_data_raw=StatsData(
            on_campus_hours=10.0, off_campus_hours=20.0, social_practice_hours=30.0
        ),
        stats_data_this_year=StatsData(
            on_campus_hours=0.5, off_campus_hours=1.0, social_practice_hours=1.5
        ),
        statement_data=[
            StatementRecord(
                name="校园清扫",
                date="2026-09-01",
                recording_mode="on",
                duration=2.5,
                person_in_charge_name="李四",
                person_in_charge_id="20200002",
            ),
            StatementRecord(
                name="School Cleanup",
                date="2026-09-02",
                recording_mode="off",
                duration=1.0,
                person_in_charge_name="Authority",
                person_in_charge_id="AUTHORITY",
            ),
        ],
        header_icon_path="./data/pwa-192x192.png",
    )
    spreadsheet = {
        "_id": "65f0000000000000000000ab",
        "name": "张三",
        "id": "20240001",
        "group": "高一(1)班",
        "on-campus": 1.5,
        "off-campus": 2.0,
        "social-practice": 3.0,
        "ay-on-campus": 0.5,
        "ay-off-campus": 1.0,
        "ay-social-practice": 1.5,
        "description": "共 6.5 小时",
    }
    return data, spreadsheet


def test_top_level_keys_and_filename():
    data, spreadsheet = _sample()
    payload = json.loads(
        statement_data_to_json(
            data, spreadsheet, "./export/高一(1)班/20240001_张三.json"
        )
    )
    assert set(payload) == {
        "user_data",
        "stats_data",
        "stats_data_raw",
        "stats_data_this_year",
        "statement_data",
        "spreadsheet",
        "filename",
    }
    assert payload["filename"] == "./export/高一(1)班/20240001_张三.json"
    assert payload["user_data"]["student_id"] == "20240001"
    assert payload["spreadsheet"]["description"] == "共 6.5 小时"


def test_stats_blocks_carry_total_hours():
    data, spreadsheet = _sample()
    payload = json.loads(statement_data_to_json(data, spreadsheet, "x.json"))
    assert payload["stats_data"]["total_hours"] == 1.5 + 2.0 + 3.0
    assert payload["stats_data_raw"]["total_hours"] == 10.0 + 20.0 + 30.0
    assert payload["stats_data_this_year"]["total_hours"] == 0.5 + 1.0 + 1.5
    for block in ("stats_data", "stats_data_raw", "stats_data_this_year"):
        assert set(payload[block]) == {
            "on_campus_hours",
            "off_campus_hours",
            "social_practice_hours",
            "total_hours",
        }


def test_statement_records_keep_all_fields():
    data, spreadsheet = _sample()
    payload = json.loads(statement_data_to_json(data, spreadsheet, "x.json"))
    assert payload["statement_data"][0] == {
        "name": "校园清扫",
        "date": "2026-09-01",
        "recording_mode": "on",
        "duration": 2.5,
        "person_in_charge_name": "李四",
        "person_in_charge_id": "20200002",
    }
    assert payload["statement_data"][1]["person_in_charge_id"] == "AUTHORITY"


def test_empty_statement_data_is_list():
    data, spreadsheet = _sample()
    data.statement_data = []
    payload = json.loads(statement_data_to_json(data, spreadsheet, "x.json"))
    assert payload["statement_data"] == []


def test_chinese_not_escaped():
    data, spreadsheet = _sample()
    raw = statement_data_to_json(
        data, spreadsheet, "./export/高一(1)班/20240001_张三.json"
    )
    assert "张三" in raw
    assert "\\u5f20" not in raw
    assert json.loads(raw)["filename"].startswith("./export/高一(1)班/")
