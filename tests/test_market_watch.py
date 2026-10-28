from datetime import datetime, timedelta, timezone
import threading

from mt5_connection import MT5ConnectionManager, MarketStructureEngine, quote_reference_time, to_datetime


def test_symbol_catalog_uses_bulk_request_and_retains_last_good_catalog():
    manager = MT5ConnectionManager("example:443", 1, "test")
    calls = []

    def bulk(method, *args, **kwargs):
        calls.append(method)
        if method == "SymbolGetArray":
            return [{"Symbol": "EURUSD", "Digits": 5, "ContractSize": 100000}]
        raise AssertionError(f"Unexpected indexed SDK call: {method}")

    manager.call = bulk
    manager._refresh_symbol_cache()
    assert list(manager.cache["symbol_cache"]) == ["EURUSD"]
    assert calls == ["SymbolGetArray"]

    manager.call = lambda method, *args, **kwargs: None if method == "SymbolGetArray" else 0
    manager._refresh_symbol_cache()
    assert list(manager.cache["symbol_cache"]) == ["EURUSD"]


def test_market_watch_does_not_label_old_tick_as_live():
    now = datetime.now(timezone.utc)

    class Connection:
        cache_lock = threading.Lock()
        capabilities = {"tick_last": True}

        def __init__(self):
            self.cache = {
                "symbol_cache": {"EURUSD": {"Symbol": "EURUSD", "Digits": 5}},
                "tick_cache": {"EURUSD": {
                    "available": True, "bid": 1.1, "ask": 1.1002,
                    "tick_time": (now - timedelta(minutes=2)).isoformat(),
                    "checked_at": now.isoformat(),
                }},
                "users": [], "server_time_msc": 0,
            }

        def prioritize_ticks(self, symbols):
            pass

    connection = Connection()
    engine = MarketStructureEngine(connection)
    stale = engine.live_watch()
    assert stale["summary"]["live_quotes"] == 0
    assert stale["summary"]["stale_quotes"] == 1
    assert stale["summary"]["average_spread_points"] == 0
    assert stale["rows"][0]["quote_status"] == "stale"

    connection.cache["tick_cache"]["EURUSD"]["tick_time"] = now.isoformat()
    engine.clear_transient_cache()
    live = engine.live_watch()
    assert live["summary"]["live_quotes"] == 1
    assert live["rows"][0]["spread_points"] == 20


def test_invalid_tick_timestamp_is_not_replaced_with_current_time():
    assert to_datetime("not-a-timestamp") is None


def test_quote_freshness_uses_mt5_server_clock_instead_of_machine_utc():
    now = datetime.now(timezone.utc)
    broker_now = now + timedelta(hours=3)
    reference = quote_reference_time(int(broker_now.timestamp() * 1000), now.isoformat(), now)
    assert abs((reference - broker_now).total_seconds()) < 0.01

    class Connection:
        cache_lock = threading.Lock()
        capabilities = {"tick_last": True}

        def __init__(self):
            self.cache = {
                "symbol_cache": {"EURUSD": {"Symbol": "EURUSD", "Digits": 5}},
                "tick_cache": {"EURUSD": {
                    "available": True, "bid": 1.1, "ask": 1.1002,
                    "tick_time": broker_now.isoformat(),
                }},
                "users": [], "server_time_msc": int(broker_now.timestamp() * 1000),
                "server_time_checked_at": now.isoformat(),
            }

        def prioritize_ticks(self, symbols):
            pass

    result = MarketStructureEngine(Connection()).live_watch()
    assert result["rows"][0]["quote_status"] == "live"


def test_zero_digit_symbols_use_the_same_points_as_spread_alerts():
    now = datetime.now(timezone.utc).isoformat()
    class Connection:
        cache_lock = threading.Lock()
        capabilities = {"tick_last": True}
        cache = {"symbol_cache": {"INDEX": {"Symbol": "INDEX", "Digits": 0}},
                 "tick_cache": {"INDEX": {"available": True, "bid": 100, "ask": 101, "tick_time": now}}}
        def prioritize_ticks(self, symbols):
            pass
    row = MarketStructureEngine(Connection()).live_watch()["rows"][0]
    assert row["digits"] == 0
    assert row["spread_points"] == 1
