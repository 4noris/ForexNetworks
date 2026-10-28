from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from services.spread_alerts import SCHEMA, SpreadAlertService, SpreadAlertSettings, fresh_spreads


@pytest.fixture
def monitor(tmp_path):
    path = tmp_path / "spread.db"
    @contextmanager
    def connection():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()
    with connection() as con:
        con.executescript(SCHEMA)
    sender = Mock(return_value=(True, "sent"))
    service = SpreadAlertService(connection, sender, lambda: True)
    service.save_settings(SpreadAlertSettings(enabled=True, default_threshold_points=25,
        symbol_thresholds={"XAUUSD.x": 50}, cooldown_seconds=60), "test")
    return service, connection, sender


def quote(symbol="XAUUSD", spread=26):
    return {"symbol": symbol, "spread": spread, "bid": 4000, "ask": 4000 + spread / 100,
            "tick_time": "2026-10-28T12:00:00+00:00"}


def test_points_use_actual_digits_zero_is_valid_and_bad_quotes_are_excluded():
    now = datetime.now(timezone.utc)
    symbols = {name: {"Digits": digits} for name, digits in (("GOLD", 2), ("ZERO", 0), ("BAD", 2), ("OLD", 2), ("FUTURE", 2), ("CROSSED", 2))}
    ticks = {name: {"available": True, "bid": 100, "ask": 101, "tick_time": now.isoformat()} for name in symbols}
    ticks["BAD"]["bid"] = float("nan")
    ticks["OLD"]["tick_time"] = (now - timedelta(seconds=6)).isoformat()
    ticks["FUTURE"]["tick_time"] = (now + timedelta(seconds=10)).isoformat()
    ticks["CROSSED"]["ask"] = 99
    rows = fresh_spreads(symbols, ticks, now)
    assert [(row["symbol"], row["spread"]) for row in rows] == [("GOLD", 100), ("ZERO", 1)]


def test_strict_threshold_exact_symbol_overrides_and_delivery(monitor):
    service, _, sender = monitor
    assert service.scan([quote(spread=25), quote("XAUUSD.x", 30)], "source", 100) == 0
    assert service.scan([quote(), quote("XAUUSD.x", 51)], "source", 100) == 2
    assert service.deliver_one("source", 101)
    sender.assert_called_once()
    assert sender.call_args.kwargs == {"route": "spread"}
    assert "Spread: 26 points" in sender.call_args.args[0]
    assert service.status()["last_alert"]["status"] == "queued"
    assert service.deliver_one("source", 102)
    assert service.status()["last_alert"]["status"] == "sent"


def test_cooldown_survives_restart_and_rapid_recovery(monitor):
    service, connection, sender = monitor
    assert service.scan([quote()], "source", 100) == 1
    assert service.scan([quote(spread=20)], "source", 101) == 0
    assert service.scan([quote()], "source", 102) == 0
    restarted = SpreadAlertService(connection, sender, lambda: True)
    assert restarted.scan([quote()], "source", 159) == 0
    assert restarted.scan([quote()], "source", 160) == 1


def test_disabled_master_missing_threshold_and_stale_queued_alerts_never_send(monitor):
    service, _, sender = monitor
    service.delivery_enabled = lambda: False
    assert service.scan([quote()], "source", 100) == 0
    assert not service.deliver_one("source", 100)
    service.delivery_enabled = lambda: True
    service.save_settings(SpreadAlertSettings(enabled=True, symbol_thresholds={"EURUSD": 10}), "test")
    assert service.scan([quote()], "source", 100) == 0
    assert service.scan([quote("EURUSD", 11)], "source", 100) == 1
    assert service.deliver_one("source", 116)
    assert service.status()["last_alert"]["status"] == "cancelled"
    sender.assert_not_called()


def test_change_rules_cancels_old_alerts_and_wrong_source_never_delivers(monitor):
    service, _, sender = monitor
    service.scan([quote()], "source", 100)
    service.save_settings(SpreadAlertSettings(enabled=True, default_threshold_points=100), "test")
    assert service.status()["last_alert"]["status"] == "cancelled"
    assert service.scan([quote(spread=101)], "new-source", 101) == 1
    assert service.deliver_one("wrong-source", 102)
    assert service.status()["last_alert"]["status"] == "cancelled"
    sender.assert_not_called()


def test_failed_delivery_is_recorded_and_does_not_flood_retries(monitor):
    service, _, sender = monitor
    sender.side_effect = RuntimeError("network failure")
    service.scan([quote()], "source", 100)
    assert service.deliver_one("source", 101)
    assert service.status()["last_alert"]["status"] == "failed"
    assert service.scan([quote(spread=30)], "source", 102) == 0
    assert sender.call_count == 1


def test_pending_queue_is_bounded_and_new_breaches_can_retry_after_drain(monitor):
    service, _, _ = monitor
    rows = [quote(f"S{i}") for i in range(140)]
    assert service.scan(rows, "source", 100) == 128
    assert service.scan(rows, "source", 101) == 0
    service.deliver_one("source", 102)
    assert service.scan(rows, "source", 103) == 1


def test_interrupted_delivery_is_not_retried_and_database_reset_disables_monitor(monitor):
    service, connection, sender = monitor
    service.scan([quote()], "source", 100)
    with connection() as con:
        con.execute("UPDATE spread_alert_events SET status='sending'")
    service.recover_interrupted()
    assert service.status()["last_alert"]["status"] == "unknown"
    assert not service.deliver_one("source", 101)
    sender.assert_not_called()
    with connection() as con:
        con.execute("DELETE FROM spread_alert_settings")
        con.execute("DELETE FROM spread_alert_state")
    service.reset_cache()
    assert service.read_settings().enabled is False
    assert service.scan([quote()], "source", 200) == 0


@pytest.mark.parametrize("values", [
    {"default_threshold_points": float("inf")}, {"symbol_thresholds": {"X": float("nan")}},
    {"symbol_thresholds": {"X": 0}}, {"symbol_thresholds": {"": 10}},
    {"symbol_thresholds": {"X": 10, " X ": 20}}, {"cooldown_seconds": 1},
])
def test_invalid_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        SpreadAlertSettings(**values)
