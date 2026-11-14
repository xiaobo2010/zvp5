"""上传管线的图片并发上限与配额键清理。

``_image_semaphore`` 原先硬编码为 2：第 3 个并发上传即排队。现在由
``ZVP5_IMAGE_CONCURRENCY``（1..8，默认 4）决定，非法值回落默认。
配额 ``_quota`` 原先只按日 key 增长、从不清理：这里钉住按日切换后只剩当天与
前 1 天的 key。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from util import attachment


@pytest.fixture(autouse=True)
def _clean_quota(monkeypatch):
    monkeypatch.setattr(attachment, "_quota", {})
    monkeypatch.setattr(attachment, "_quota_day", None)
    yield


def test_image_concurrency_is_configurable_and_defensive(monkeypatch):
    monkeypatch.delenv("ZVP5_IMAGE_CONCURRENCY", raising=False)
    assert attachment.image_concurrency() == 4
    assert attachment.IMAGE_CONCURRENCY_DEFAULT == 4

    monkeypatch.setenv("ZVP5_IMAGE_CONCURRENCY", "1")
    assert attachment.image_concurrency() == 1
    monkeypatch.setenv("ZVP5_IMAGE_CONCURRENCY", "8")
    assert attachment.image_concurrency() == 8

    for invalid in ("0", "9", "-1", "abc", "", "3.5"):
        monkeypatch.setenv("ZVP5_IMAGE_CONCURRENCY", invalid)
        assert attachment.image_concurrency() == attachment.IMAGE_CONCURRENCY_DEFAULT


def test_process_upload_never_exceeds_the_concurrency_cap(monkeypatch):
    monkeypatch.setattr(attachment, "_image_semaphore", asyncio.Semaphore(2))
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def fake_process(raw: bytes) -> bytes:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.03)
        with lock:
            state["current"] -= 1
        return raw

    monkeypatch.setattr(attachment, "_process_image_sync", fake_process)

    async def run():
        return await asyncio.gather(
            *[attachment.process_upload(b"x") for _ in range(8)]
        )

    assert asyncio.run(run()) == [b"x"] * 8
    assert state["peak"] == 2


def test_stale_quota_keys_are_pruned_on_day_rollover(monkeypatch):
    monkeypatch.setattr(attachment, "_today", lambda: "2026-01-10")
    monkeypatch.setattr(attachment, "_quota_day", "2026-01-09")
    attachment._quota.update(
        {
            ("2026-01-07", "u1"): [9, 90],
            ("2026-01-08", "u1"): [1, 10],
            ("2026-01-09", "u1"): [2, 20],
            ("2026-01-10", "u2"): [4, 40],
        }
    )

    attachment.record_upload("u1", 5)

    assert set(attachment._quota) == {
        ("2026-01-09", "u1"),
        ("2026-01-10", "u1"),
        ("2026-01-10", "u2"),
    }
    assert attachment._quota[("2026-01-10", "u1")] == [1, 5]
    assert attachment._quota_day == "2026-01-10"


def test_check_quota_also_prunes_stale_keys(monkeypatch):
    monkeypatch.setattr(attachment, "_today", lambda: "2026-01-10")
    monkeypatch.setattr(attachment, "_quota_day", "2026-01-05")
    attachment._quota.update(
        {
            ("2026-01-06", "u1"): [1, 10],
            ("2026-01-10", "u1"): [0, 0],
        }
    )

    attachment.check_quota("u1")

    assert set(attachment._quota) == {("2026-01-10", "u1")}


def test_same_day_calls_do_not_drop_todays_usage(monkeypatch):
    monkeypatch.setattr(attachment, "_today", lambda: "2026-01-10")
    monkeypatch.setattr(attachment, "_quota_day", "2026-01-10")

    attachment.record_upload("u1", 7)
    attachment.record_upload("u1", 3)

    assert attachment._quota[("2026-01-10", "u1")] == [2, 10]
