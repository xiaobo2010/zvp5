"""连接池策略的纯配置测试：默认值、环境变量覆盖、以及真正传给 create_async_engine 的参数。

全程不连库 —— ``create_async_engine`` 被打桩，``_pool_settings()`` 直接断言。
这么测的原因：这个文件的全部风险都在「参数被谁悄悄写死了」，比如 pre_ping
重新被硬编码回 True，或者 recycle 又变回 1800；只要能看见最终 kwargs，
就不需要一条真连接来证明。
"""

from __future__ import annotations

import pytest

from db.oracle import engine as db_engine

_POOL_ENV = (
    "ZVMS_DB_POOL_SIZE",
    "ZVMS_DB_POOL_OVERFLOW",
    "ZVMS_DB_POOL_TIMEOUT",
    "ZVMS_DB_PRE_PING",
    "ZVMS_DB_RECYCLE",
)


@pytest.fixture
def clean_pool_env(monkeypatch):
    """清掉所有池相关环境变量，让用例从「什么都没设」的默认态出发。"""
    for name in _POOL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _FakeEngine:
    """create_async_engine 的替身，只记住参数供断言。"""

    def __init__(self, *args, **kwargs):
        self.url = args[0] if args else None
        self.kwargs = kwargs


class TestPoolDefaults:
    def test_pre_ping_off_by_default(self, clean_pool_env):
        # 跨洋链路：pre_ping 每次 checkout 多一个 RTT（279ms -> 416ms），默认必须关。
        assert db_engine._pool_settings()["pool_pre_ping"] is False

    def test_recycle_defaults_to_600(self, clean_pool_env):
        # 关掉 pre_ping 后靠主动老化兜住被对端掐断的连接，600s 是保守值。
        assert db_engine._pool_settings()["pool_recycle"] == 600

    def test_existing_pool_sizing_unchanged(self, clean_pool_env):
        settings = db_engine._pool_settings()
        assert settings["pool_size"] == 8
        assert settings["max_overflow"] == 8
        assert settings["pool_timeout"] == 5.0
        assert settings["pool_use_lifo"] is True

    def test_defaults_do_not_leak_extra_keys(self, clean_pool_env):
        # 多出来的键会以 **_pool_settings() 的形式进 create_async_engine，拼错即 TypeError。
        assert set(db_engine._pool_settings()) == {
            "pool_size",
            "max_overflow",
            "pool_timeout",
            "pool_use_lifo",
            "pool_pre_ping",
            "pool_recycle",
        }


class TestEnvOverrides:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_pre_ping_truthy_values(self, clean_pool_env, raw):
        clean_pool_env.setenv("ZVMS_DB_PRE_PING", raw)
        assert db_engine._pool_settings()["pool_pre_ping"] is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_pre_ping_falsy_values(self, clean_pool_env, raw):
        clean_pool_env.setenv("ZVMS_DB_PRE_PING", raw)
        assert db_engine._pool_settings()["pool_pre_ping"] is False

    def test_recycle_override(self, clean_pool_env):
        clean_pool_env.setenv("ZVMS_DB_RECYCLE", "1200")
        assert db_engine._pool_settings()["pool_recycle"] == 1200

    def test_sizing_overrides_still_work(self, clean_pool_env):
        clean_pool_env.setenv("ZVMS_DB_POOL_SIZE", "4")
        clean_pool_env.setenv("ZVMS_DB_POOL_OVERFLOW", "2")
        clean_pool_env.setenv("ZVMS_DB_POOL_TIMEOUT", "1.5")
        settings = db_engine._pool_settings()
        assert settings["pool_size"] == 4
        assert settings["max_overflow"] == 2
        assert settings["pool_timeout"] == 1.5


class TestGetEngineWiring:
    @pytest.fixture
    def captured(self, clean_pool_env, monkeypatch):
        """打桩 create_async_engine + LOB hook，并把全局 _engine 复位。"""
        calls: list[_FakeEngine] = []

        def fake_create(url, **kwargs):
            fake = _FakeEngine(url, **kwargs)
            calls.append(fake)
            return fake

        monkeypatch.setattr(db_engine, "create_async_engine", fake_create)
        monkeypatch.setattr(db_engine, "install_lob_binds", lambda _e: None)
        monkeypatch.setattr(db_engine, "_engine", None)
        return calls

    def test_defaults_reach_create_async_engine(self, captured):
        db_engine.get_engine()
        assert len(captured) == 1
        kwargs = captured[0].kwargs
        assert kwargs["pool_pre_ping"] is False
        assert kwargs["pool_recycle"] == 600
        assert kwargs["pool_size"] == 8
        assert kwargs["max_overflow"] == 8
        assert kwargs["pool_timeout"] == 5.0
        assert kwargs["pool_use_lifo"] is True
        assert kwargs["hide_parameters"] is True

    def test_env_overrides_reach_create_async_engine(self, captured, clean_pool_env):
        clean_pool_env.setenv("ZVMS_DB_PRE_PING", "1")
        clean_pool_env.setenv("ZVMS_DB_RECYCLE", "90")
        db_engine.get_engine()
        kwargs = captured[0].kwargs
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_recycle"] == 90

    def test_no_hardcoded_pool_policy_in_get_engine(self, captured):
        # 防回归：get_engine 里不得再出现 pool_pre_ping / pool_recycle 字面量，
        # 否则会和 **_pool_settings() 撞成 "got multiple values for keyword argument"。
        db_engine.get_engine()
        assert "pool_pre_ping" in captured[0].kwargs

    def test_engine_is_cached(self, captured, clean_pool_env):
        first = db_engine.get_engine()
        second = db_engine.get_engine()
        assert first is second
        assert len(captured) == 1
