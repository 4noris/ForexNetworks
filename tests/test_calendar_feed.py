from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import api
import calendar_feed


def test_calendar_api_fetches_and_caches_server_dated_events(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_feed, "_api_cache_file", lambda: tmp_path / "calendar.json")
    monkeypatch.setattr(calendar_feed, "_calendar_cache", None)
    monkeypatch.setattr(calendar_feed, "_calendar_checked_at", 0.0)
    calls = []
    payload = [{"date": "2026-10-13T22:30:00Z", "country": "USD", "title": "CPI",
                "impact": "High", "forecast": "0.3%", "previous": "0.2%"}]
    def get(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(calendar_feed.requests, "get", get)
    first = calendar_feed.read_calendar_api()
    second = calendar_feed.read_calendar_api()
    assert first["status"] == second["status"] == "connected"
    assert first["events"][0]["server_time"] == "2026.10.14 01:30"
    assert first["events"][0]["time"] == "2026-10-13T22:30:00+00:00"
    assert len(calls) == 1


def test_terminal_calendar_export_keeps_events_visible_when_stale(tmp_path, monkeypatch):
    path = tmp_path / "RMS_EconomicCalendar.tsv"
    stamp = int(datetime.now(timezone.utc).timestamp())
    path.write_text(
        "utc_ts\tserver_time\tcurrency\ttitle\timpact\ttime_mode\tperiod\tactual\tforecast\tprevious\n"
        f"{stamp}\t2026.10.14 17:30\tUSD\tCPI m/m\thigh\ttimed\tSep\t0.4\t0.3\t0.2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(calendar_feed, "calendar_file", lambda: path)
    live = calendar_feed.read_terminal_calendar()
    assert live["status"] == "connected"
    assert live["events"][0]["title"] == "CPI m/m"
    assert live["events"][0]["time"].endswith("+00:00")
    assert live["events"][0]["server_time"] == "2026.10.14 17:30"
    assert live["server_today"] == "2026-10-14"
    stale = calendar_feed.read_terminal_calendar(max_age_seconds=-1)
    assert stale["status"] == "stale"
    assert len(stale["events"]) == 1


def test_news_trading_automatically_uses_calendar_api():
    opened = int(datetime.now(timezone.utc).timestamp()) - 120
    deals = [
        {"Deal": 1, "PositionID": 77, "Login": 1001, "Action": 0, "Entry": 0, "Time": opened, "Symbol": "EURUSD", "Volume": 10000},
        {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": 1, "Entry": 1, "Time": opened + 60, "Symbol": "EURUSD", "Volume": 10000, "Profit": 25},
    ]
    event = {"time": datetime.fromtimestamp(opened - 10, timezone.utc).isoformat(), "currency": "USD", "title": "CPI", "impact": "high"}
    with (
        patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
        patch.object(api, "request_deals_by_group", return_value=(deals, None)),
        patch.object(api, "request_positions_by_group", return_value=([], None)),
        patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
        patch.object(api, "read_calendar_api", return_value={"events": [event], "status": "connected", "message": ""}),
    ):
        report = api.build_antifraud_snapshot(object(), ["live"])
    alerts = [row for row in report["events"] if row["detector"] == "news_trading"]
    assert len(alerts) == 1
    assert alerts[0]["data_quality"] == "VERIFIED"
    assert alerts[0]["triggering_values"]["currency"] == "USD"


def test_news_impact_selection_filters_before_nearest_release():
    opened = int(datetime.now(timezone.utc).timestamp()) - 120
    deals = [
        {"Deal": 1, "PositionID": 77, "Login": 1001, "Action": 0, "Entry": 0, "Time": opened, "Symbol": "EURUSD", "Volume": 10000},
        {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": 1, "Entry": 1, "Time": opened + 60, "Symbol": "EURUSD", "Volume": 10000, "Profit": 25},
    ]
    events = [
        {"time": datetime.fromtimestamp(opened - offset, timezone.utc).isoformat(),
         "currency": "USD", "title": title, "impact": impact}
        for offset, title, impact in [(50, "High release", "high"), (10, "Low release", "low")]
    ]
    with (
        patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
        patch.object(api, "request_deals_by_group", return_value=(deals, None)),
        patch.object(api, "request_positions_by_group", return_value=([], None)),
        patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
        patch.object(api, "read_calendar_api", return_value={"events": events, "status": "connected", "message": ""}),
    ):
        high = api.build_antifraud_snapshot(object(), ["live"], news_impacts=["high"])
        low = api.build_antifraud_snapshot(object(), ["live"], news_impacts=["low"])
        medium = api.build_antifraud_snapshot(object(), ["live"], news_impacts=["medium"])
    high_alert = next(event for event in high["events"] if event["detector"] == "news_trading")
    low_alert = next(event for event in low["events"] if event["detector"] == "news_trading")
    assert "High release" in high_alert["evidence"]
    assert high_alert["thresholds"]["impacts"] == ["high"]
    assert "Low release" in low_alert["evidence"]
    assert not any(event["detector"] == "news_trading" for event in medium["events"])
    assert next(row for row in medium["detectors"] if row["key"] == "news_trading")["status"] == "no_data"


def test_all_day_calendar_entry_is_visible_but_not_used_for_trade_timing():
    now = int(datetime.now(timezone.utc).timestamp())
    rows = [
        {"time": datetime.fromtimestamp(now, timezone.utc).isoformat(), "currency": "USD", "title": "Holiday", "impact": "low", "time_mode": "all_day"},
    ]
    assert api.normalize_calendar_events(rows, now - 60, now + 60) == []
