from services import markout
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch

import api
import pytest


def test_signed_markout_and_execution_spread_are_separate():
    start = 1_800_000_000_000
    deals = [
        {"Deal": 1, "Login": 900102, "Action": 0, "TimeMsc": start, "Price": 100.1, "Symbol": "EURUSD"},
        {"Deal": 2, "Login": 900102, "Action": 1, "TimeMsc": start, "Price": 99.9, "Symbol": "EURUSD"},
        {"Deal": 2, "Login": 900102, "Action": 1, "TimeMsc": start, "Price": 99.9, "Symbol": "EURUSD"},
        {"Deal": 3, "Login": 99999, "Action": 0, "TimeMsc": start, "Price": 100, "Symbol": "EURUSD"},
    ]
    executions = markout.normalize_executions(deals, 900102, start, start + 1000)
    assert [row["deal"] for row in executions] == [1, 2]
    ticks = [
        {"TimeMsc": start - 500, "Bid": 99.9, "Ask": 100.1},
        {"TimeMsc": start + 1000, "Bid": 100.9, "Ask": 101.1},
    ]
    result = markout.calculate_markouts(executions, {"EURUSD": ticks}, (1,))
    assert result["executions"][0]["horizons"]["1"]["client_mid_bps"] == 100
    assert result["executions"][1]["horizons"]["1"]["client_mid_bps"] == -100
    assert result["executions"][0]["horizons"]["1"]["execution_to_mid_bps"] < 100
    assert result["summary"]["1"]["average_client_mid_bps"] == 0


def test_missing_or_stale_quotes_never_become_zero_markout():
    start = 1_800_000_000_000
    deal = {"Deal": 1, "Login": 900102, "Action": 0, "TimeMsc": start, "Price": 100, "Symbol": "EURUSD"}
    rows = markout.normalize_executions([deal], 900102, start, start)
    future_only = [{"TimeMsc": start + 1000, "Bid": 101, "Ask": 101}]
    result = markout.calculate_markouts(rows, {"EURUSD": future_only}, (1,))
    assert result["executions"][0]["horizons"]["1"]["client_mid_bps"] is None
    assert result["executions"][0]["horizons"]["1"]["status"] == "missing_arrival_quote"
    assert result["summary"]["1"]["average_client_mid_bps"] is None
    stale_future = [
        {"TimeMsc": start - 500, "Bid": 99, "Ask": 101},
        {"TimeMsc": start + 5000, "Bid": 102, "Ask": 102},
    ]
    result = markout.calculate_markouts(rows, {"EURUSD": stale_future}, (1,))
    assert result["executions"][0]["horizons"]["1"]["status"] == "missing_future_quote"
    assert result["summary"]["1"]["covered"] == 0


def test_crossed_or_invalid_ticks_are_ignored():
    times, mids = markout.normalize_ticks([
        {"TimeMsc": 1000, "Bid": 101, "Ask": 100},
        {"TimeMsc": 2000, "Bid": 100, "Ask": 101},
        {"TimeMsc": 3000, "Bid": float("nan"), "Ask": 101},
    ])
    assert times == [2000]
    assert mids == [100.5]


def test_api_scopes_one_login_and_leaves_missing_tick_history_unavailable():
    selected_day = date(2026, 10, 13)
    stamp = int(datetime(2026, 10, 13, 12, tzinfo=timezone.utc).timestamp())

    @contextmanager
    def fake_session():
        yield object()

    deal = {"Deal": 91, "Login": 900102, "Action": 0, "Time": stamp,
            "Price": 1.2, "Symbol": "EURUSD"}
    with (patch.object(api, "yielding_history_manager_session", fake_session),
          patch.object(api, "_collect_user_scope", return_value=({900102: {"name": "Client"}}, {900102: "real"}, [])),
          patch.object(api, "request_deals_by_login", return_value=([deal], None)) as deals,
          patch.object(api, "request_tick_history", return_value=([], "Ticks unavailable")) as ticks):
        response = api.client_markout(api.MarkoutRequest(client_login=900102,
            selected_groups=["real"], from_date=selected_day, to_date=selected_day))

    assert deals.call_count == 1
    assert deals.call_args.args[1] == 900102
    assert ticks.call_count == 1
    assert response["summary"]["60"]["covered"] == 0
    assert response["summary"]["60"]["average_client_mid_bps"] is None
    assert "Ticks unavailable" in response["warnings"][0]


def test_tick_request_rejects_status_without_quotes():
    class StatusOnly:
        def TickHistoryRequest(self, *_args):
            return True

    ticks, error = api.request_tick_history(StatusOnly(), "EURUSD", 1, 2)
    assert ticks == []
    assert "no tick array" in error


def test_conflicting_duplicate_ticket_is_rejected():
    first = {"Deal": 7, "Login": 900102, "Action": 0, "Time": 1_800_000_000,
             "Price": 100, "Symbol": "EURUSD"}
    with pytest.raises(ValueError, match="conflicting execution"):
        markout.normalize_executions([first, {**first, "Price": 101}], 900102,
                                     1_800_000_000_000, 1_800_000_001_000)


def test_manager_small_volume_is_not_treated_as_whole_lots():
    deal = {"Deal": 1, "Login": 900102, "Action": 0, "Time": 1_800_000_000,
            "Price": 100, "Symbol": "EURUSD", "Volume": 1}
    rows = markout.normalize_executions([deal], 900102, 1_800_000_000_000, 1_800_000_000_999)
    assert rows[0]["volume_lots"] == 0.0001


def test_eight_inclusive_utc_days_fail_before_any_manager_read():
    from fastapi import HTTPException
    with patch.object(api, "yielding_history_manager_session") as session:
        with pytest.raises(HTTPException) as failure:
            api.client_markout(api.MarkoutRequest(client_login=900102, selected_groups=["real"],
                from_date=date(2026, 10, 1), to_date=date(2026, 10, 8)))
        assert failure.value.detail == "Select at most seven UTC days, including both dates."
        session.assert_not_called()


def test_900_second_horizon_preserves_missing_quote_as_unavailable():
    start = 1_800_000_000_000
    deal = {"Deal": 1, "Login": 900102, "Action": 0, "TimeMsc": start, "Price": 100, "Symbol": "EURUSD"}
    rows = markout.normalize_executions([deal], 900102, start, start)
    result = markout.calculate_markouts(rows, {"EURUSD": [
        {"TimeMsc": start, "Bid": 99, "Ask": 101},
        {"TimeMsc": start + 900_000, "Bid": 100, "Ask": 102}]})
    assert result["summary"]["900"]["covered"] == 1
    assert result["summary"]["600"]["average_client_mid_bps"] is None


def test_empty_report_returns_recent_trading_day_without_fabricating_markout():
    selected = date(2026, 10, 28)
    latest = int(datetime(2026, 10, 27, 12, tzinfo=timezone.utc).timestamp())
    @contextmanager
    def session():
        yield object()
    deal = {"Deal": 123, "Login": 900101, "Action": 0, "Time": latest, "Price": 100, "Symbol": "XAUUSD"}
    with (patch.object(api, "yielding_history_manager_session", session),
          patch.object(api, "_resolve_groups_for_request", return_value=(["real"], [], [])),
          patch.object(api, "_collect_user_scope", return_value=({900101: {}}, {900101: "real"}, [])),
          patch.object(api, "request_deals_by_login", side_effect=[([], None), ([deal], None)]) as deals,
          patch.object(api, "request_tick_history") as ticks):
        result = api._calculate_client_markout(api.MarkoutRequest(client_login=900101,
            selected_groups=["real"], from_date=selected, to_date=selected))
    assert result["history_status"] == "no_deals"
    assert result["executions"] == []
    assert result["diagnostics"]["latest_execution_date"] == "2026-10-27"
    assert result["diagnostics"]["recent_lookup_from"] == "2026-10-22"
    assert result["dashboard"]["dpm"] is None
    assert deals.call_count == 2
    ticks.assert_not_called()


def test_not_found_is_empty_history_but_other_sdk_failures_are_errors():
    class EmptyHistory:
        def DealRequestByLogins(self, *_args):
            return False
    with patch.object(api.brokerage_pnl, "_last_error_code", return_value=13):
        assert api.request_deals_by_login(EmptyHistory(), 900101, 1, 2) == ([], None)
    with patch.object(api.brokerage_pnl, "_last_error_code", return_value=6):
        rows, error = api.request_deals_by_login(EmptyHistory(), 900101, 1, 2)
        assert rows == [] and error
