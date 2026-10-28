from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from unittest.mock import patch

import api


def test_focused_performance_loads_daily_without_full_account_snapshot(monkeypatch, tmp_path):
    import mt5_connection

    class Manager:
        is_connected = True

        def group_cache_view(self):
            return ["real\\live"]

        def snapshot(self):
            raise AssertionError("focused mode must not copy a full account snapshot")

    @contextmanager
    def history_session():
        yield object()

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(api, "DB_PATH", tmp_path / "performance-focused.db")
    api.init_db()
    api._PERFORMANCE_CACHE.clear()
    api._PERFORMANCE_REFRESHING.clear()
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", False)
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: Manager())
    monkeypatch.setattr(api, "yielding_history_manager_session", history_session)
    monkeypatch.setattr(api.threading, "Thread", ImmediateThread)
    observed = []

    def build(_manager, groups, _excluded, days, cached_users, request_groups=None):
        observed.append((days, cached_users, request_groups))
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "periods": {"daily": {"net_pnl": 42}}, "data_quality": {}, "warnings": []}

    monkeypatch.setattr(api, "build_performance_snapshot", build)
    request = api.PerformanceRequest(selected_groups=["real\\live"], lookback_days=31)
    api.get_risk_performance(request)
    result = api.get_risk_performance(request)

    assert result["periods"]["daily"]["net_pnl"] == 42
    assert observed == [(1, None, ["real\\live"]), (31, None, ["real\\live"])]
    api._PERFORMANCE_CACHE.clear()


def test_performance_uses_closing_deals_without_requiring_open_lifecycle():
    now = int(datetime.now(timezone.utc).timestamp())
    users = {
        1001: {"name": "Winner", "group": "real"},
        1002: {"name": "Loser", "group": "real"},
    }
    deals = [
        {"Deal": 1, "Login": 1001, "Action": api.DEAL_BUY, "Entry": api.ENTRY_OUT,
         "Time": now, "Volume": 10000, "Profit": 100, "Commission": -2, "Fee": -1, "Swap": -3},
        {"Deal": 2, "Login": 1002, "Action": api.DEAL_SELL, "Entry": api.ENTRY_OUT,
         "Time": now, "Volume": 10000, "Profit": -50, "Commission": -2},
        {"Deal": 3, "Login": 1001, "Action": api.DEAL_BUY, "Entry": 0,
         "Time": now, "Volume": 10000, "Profit": 0, "Commission": -2},
    ]

    with (
        patch.object(api, "_collect_user_scope", return_value=(users, {1001: "real", 1002: "real"}, [])),
        patch.object(api, "_fetch_scoped_deals", return_value=(deals, [])),
    ):
        result = api.build_performance_snapshot(object(), ["real"])

    daily = result["periods"]["daily"]
    assert daily["net_pnl"] == 42.0
    assert daily["winners"] == [{"login": 1001, "name": "Winner", "group": "real", "pnl": 94.0, "trades": 1}]
    assert daily["losers"] == [{"login": 1002, "name": "Loser", "group": "real", "pnl": -52.0, "trades": 1}]
    assert result["data_quality"]["trade_deals"] == 3
    assert result["data_quality"]["closing_deals"] == 2


def test_performance_accepts_realized_trade_when_wrapper_omits_entry():
    now = int(datetime.now(timezone.utc).timestamp())
    users = {1001: {"name": "Winner", "group": "real"}}
    deals = [{"Login": 1001, "Type": "buy", "Time": now, "Volume": 1.0, "Profit": 25.0}]

    with (
        patch.object(api, "_collect_user_scope", return_value=(users, {1001: "real"}, [])),
        patch.object(api, "_fetch_scoped_deals", return_value=(deals, [])),
    ):
        result = api.build_performance_snapshot(object(), ["real"])

    assert result["periods"]["daily"]["net_pnl"] == 25.0
    assert result["data_quality"]["closing_deals"] == 1


def test_performance_cache_is_canonical_and_returns_isolated_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "canonical-performance.db")
    api.init_db()
    left = api.PerformanceRequest(
        selected_groups=["real\\vip", "real\\standard"],
        excluded_logins=[20, 10],
        lookback_days=365,
    )
    right = api.PerformanceRequest(
        selected_groups=["real\\standard", "real\\vip"],
        excluded_logins=[10, 20],
        lookback_days=365,
    )
    assert api._performance_cache_key(left) == api._performance_cache_key(right)

    key = api._performance_cache_key(left)
    api._PERFORMANCE_CACHE.clear()
    source = {"periods": {"daily": {"net_pnl": 42.0}}}
    api._store_cached_performance(key, source)
    cached = api._get_cached_performance(key)
    assert cached["cache"]["hit"] is True
    cached["periods"]["daily"]["net_pnl"] = 0
    assert api._get_cached_performance(key)["periods"]["daily"]["net_pnl"] == 42.0
    api._PERFORMANCE_CACHE.clear()


def test_performance_cache_can_serve_stale_while_refreshing(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "performance.db")
    api.init_db()
    request = api.PerformanceRequest(selected_groups=["real"], lookback_days=365)
    key = api._performance_cache_key(request)
    api._PERFORMANCE_CACHE.clear()
    api._store_cached_performance(key, {
        "overall_window_days": 365,
        "periods": {"daily": {"net_pnl": 19.5}},
    })
    api._PERFORMANCE_CACHE.clear()

    assert api._get_cached_performance(key) is None
    stale = api._get_cached_performance(key, allow_stale=True)

    assert stale["periods"]["daily"]["net_pnl"] == 19.5
    assert stale["cache"]["persistent"] is True
    assert stale["cache"]["stale"] is True


def test_scoped_deals_prefers_bulk_login_history_for_many_groups():
    class BulkHistoryAPI:
        def __init__(self):
            self.requests = []

        def DealRequestByLoginsNumPy(self, logins, from_ts, to_ts, fields):
            self.requests.append((list(logins), from_ts, to_ts, fields))
            return [{"Deal": 7, "Login": 1001, "Action": api.DEAL_BUY, "Profit": 10}]

        def DealRequestByLogins(self, *_args):
            raise AssertionError("object-based bulk fallback should not run")

        def DealRequestByGroup(self, *_args):
            raise AssertionError("per-group fallback should not run")

    manager = BulkHistoryAPI()
    rows, warnings = api._fetch_scoped_deals(
        manager, ["real\\one", "real\\two"], 100, 200, {1001, 1002}
    )

    assert warnings == []
    assert [row["Deal"] for row in rows] == [7]
    assert len(manager.requests) == 1
    assert manager.requests[0][:3] == ([1001, 1002], 100, 200)
    assert "Profit" in manager.requests[0][3]
