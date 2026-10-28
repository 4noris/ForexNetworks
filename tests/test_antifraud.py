from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class AntiFraudTests(unittest.TestCase):
    def setUp(self):
        api._TELEGRAM_SENT.clear()

    def test_telegram_delivers_one_message_per_account(self):
        delivered = []

        class ImmediateThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with (
            patch.object(api.threading, "Thread", ImmediateThread),
            patch.object(
                api,
                "send_telegram_notification",
                side_effect=lambda message: delivered.append(message) or (True, "sent"),
            ),
        ):
            api._send_telegram_messages(["Account #1001", "Account #1002"])

        self.assertEqual(delivered, ["Account #1001", "Account #1002"])

    def test_latency_event_requires_real_tick_gap_and_profitable_short_trade(self):
        opened = 1_800_000_000
        deals = [
            {"Deal": 1, "PositionID": 77, "Login": 1001, "Action": 0, "Entry": 0, "Time": opened, "TimeMsc": opened * 1000, "Symbol": "XAUUSD", "Volume": 10000, "Price": 2000},
            {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": 1, "Entry": 1, "Time": opened + 60, "Symbol": "XAUUSD", "Volume": 10000, "Price": 2001, "Profit": 25},
        ]
        ticks = [
            {"datetime_msc": (opened - 5) * 1000, "bid": 1999.9, "ask": 2000.0},
            {"datetime_msc": opened * 1000, "bid": 2000.0, "ask": 2000.1},
        ]

        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=(deals, None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
            patch.object(api, "request_tick_history", return_value=(ticks, None)),
        ):
            result = api.build_antifraud_snapshot(
                object(), ["live"], lookback_hours=24, gap_seconds=3,
                profit_threshold=10, maximum_duration_minutes=5,
            )

        latency = [event for event in result["events"] if event["detector"] == "latency_arbitrage"]
        self.assertEqual(len(latency), 1)
        self.assertEqual(latency[0]["position_id"], 77)
        self.assertIn("5.0s tick gap", latency[0]["evidence"])
        self.assertEqual(result["summary"]["latency_checked"], 1)
        self.assertEqual(latency[0]["data_quality"], "VERIFIED")
        self.assertEqual(latency[0]["severity"], "warning")
        self.assertEqual(latency[0]["category"], "fraud_indicator")

    def test_unavailable_sources_are_not_reported_as_active(self):
        with (
            patch.object(api, "_collect_user_scope", return_value=({}, {}, [])),
            patch.object(api, "request_deals_by_group", return_value=([], None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        detectors = {row["key"]: row for row in result["detectors"]}
        self.assertEqual(detectors["rejected_orders"]["status"], "data_required")
        self.assertEqual(detectors["cid_control"]["status"], "data_required")
        self.assertEqual(detectors["rejected_orders"]["events"], 0)

    def test_balance_below_credit_alerts_inactive_accounts_without_guessing_missing_values(self):
        users = {login: {"name": f"Client {login}", "currency": "USD"} for login in range(1001, 1005)}
        accounts = {
            1001: {"Login": 1001, "Balance": 80, "Credit": 100},
            1002: {"Login": 1002, "Balance": 100, "Credit": 100},
            1003: {"Login": 1003, "Balance": -10, "Credit": 0},
            1004: {"Login": 1004, "Balance": 10},
        }
        with (
            patch.object(api, "_collect_user_scope", return_value=(users, {login: "live" for login in users}, [])),
            patch.object(api, "request_deals_by_group", return_value=([], None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_groups", return_value=(accounts, [])) as account_request,
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        account_request.assert_called_once()
        self.assertEqual(account_request.call_args.args[2], set(users))
        alerts = [event for event in result["events"] if event["detector"] == "balance_below_credit"]
        self.assertEqual([event["login"] for event in alerts], [1001])
        self.assertEqual(alerts[0]["severity"], "high")
        self.assertEqual(alerts[0]["category"], "fraud_indicator")
        self.assertEqual(alerts[0]["triggering_values"]["shortfall"], 20)
        self.assertEqual(alerts[0]["data_quality"], "VERIFIED")
        detector = next(row for row in result["detectors"] if row["key"] == "balance_below_credit")
        self.assertEqual(detector["events"], 1)
        self.assertEqual(detector["category"], "Fraud indicators")
        self.assertEqual(detector["status"], "active")

    def test_ip_and_affiliate_signals_use_mt5_user_fields(self):
        users = {
            1001: {"name": "Agent", "last_ip": "8.8.8.8", "agent_login": 0},
            1002: {"name": "Client", "last_ip": "8.8.8.8", "agent_login": 1001},
            1003: {"name": "Unknown IP", "last_ip": "", "agent_login": 0},
        }
        with (
            patch.object(api, "_collect_user_scope", return_value=(users, {login: "live" for login in users}, [])),
            patch.object(api, "request_deals_by_group", return_value=([], None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_groups", return_value=({}, [])),
            patch.object(api, "read_calendar_api", return_value={"events": [], "status": "error", "message": "Unavailable"}),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])
        ip_events = [event for event in result["events"] if event["detector"] == "ip_control"]
        affiliate_events = [event for event in result["events"] if event["detector"] == "affiliate_cheating"]
        assert {event["login"] for event in ip_events} == {1001, 1002}
        assert all(event["data_quality"] == "PARTIAL" for event in ip_events)
        assert [event["login"] for event in affiliate_events] == [1002]
        assert affiliate_events[0]["triggering_values"]["agent_login"] == 1001
        assert all(event["login"] != 1003 for event in ip_events + affiliate_events)
        statuses = {row["key"]: row["status"] for row in result["detectors"]}
        assert statuses["ip_control"] == statuses["affiliate_cheating"] == "active"

    def test_deal_detectors_report_no_data_instead_of_false_healthy(self):
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=([], None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        detectors = {row["key"]: row for row in result["detectors"]}
        self.assertEqual(detectors["scalpers_hft"]["status"], "no_data")
        self.assertEqual(detectors["lifetime_deposit_high_profit"]["status"], "no_data")
        self.assertTrue(any("No MT5 deals were returned" in warning for warning in result["warnings"]))

    def test_news_trading_uses_verified_news_time(self):
        opened = int(api.datetime.now(api.timezone.utc).timestamp()) - 120
        deals = [
            {"Deal": 1, "PositionID": 77, "Login": 1001, "Action": 0, "Entry": 0, "Time": opened, "Symbol": "XAUUSD", "Volume": 10000},
            {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": 1, "Entry": 1, "Time": opened + 60, "Symbol": "XAUUSD", "Volume": 10000, "Profit": 25},
        ]
        news = [{"time": api.datetime.fromtimestamp(opened - 15, api.timezone.utc).isoformat(), "title": "Central bank decision", "currency": "XAU", "impact": "high"}]
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=(deals, None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
            patch.object(api, "request_tick_history", return_value=([], None)),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"], news_window_minutes=5, calendar_events=news)

        events = [event for event in result["events"] if event["detector"] == "news_trading"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["severity"], "high")
        self.assertIn("15s after", events[0]["evidence"])
        self.assertIn("Central bank decision", events[0]["evidence"])

    def test_news_trading_ignores_other_currency_and_requires_calendar(self):
        opened = int(api.datetime.now(api.timezone.utc).timestamp()) - 120
        deals = [
            {"Deal": 1, "PositionID": 77, "Login": 1001, "Action": 0, "Entry": 0, "Time": opened, "Symbol": "EURUSD", "Volume": 10000},
            {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": 1, "Entry": 1, "Time": opened + 60, "Symbol": "EURUSD", "Volume": 10000, "Profit": 25},
        ]
        calendar = [{"time": api.datetime.fromtimestamp(opened, api.timezone.utc).isoformat(), "title": "CPI", "currency": "JPY", "impact": "high"}]
        with (
            patch.object(api, "read_calendar_api", return_value={"events": [], "status": "error", "message": "Calendar unavailable"}),
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=(deals, None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"], calendar_events=calendar)
            missing = api.build_antifraud_snapshot(object(), ["live"])
        self.assertFalse(any(event["detector"] == "news_trading" for event in result["events"]))
        self.assertEqual(next(row for row in missing["detectors"] if row["key"] == "news_trading")["status"], "data_required")

    def test_missing_equity_is_not_replaced_with_balance(self):
        positions = [{
            "Position": 10, "Login": 1001, "Symbol": "EURUSD", "Action": 0,
            "Volume": 10000, "Profit": 0,
        }]
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=([], None)),
            patch.object(api, "request_positions_by_group", return_value=(positions, None)),
            patch.object(api, "request_accounts_by_logins", return_value=({1001: {"Balance": 10}}, [])),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        detectors = {event["detector"] for event in result["events"]}
        summary = next(row for row in result["client_summaries"] if row["login"] == 1001)
        self.assertNotIn("mini_accounts", detectors)
        self.assertNotIn("failed_stopouts", detectors)
        self.assertIsNone(summary["equity"])

    def test_low_deposit_high_profit_uses_real_balance_posting(self):
        opened = 1_800_000_000
        deals = [
            {"Deal": 1, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": opened - 10, "Profit": 100},
            {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": api.DEAL_BUY, "Entry": 0, "Time": opened, "Symbol": "XAUUSD", "Volume": 10000},
            {"Deal": 3, "PositionID": 77, "Login": 1001, "Action": api.DEAL_SELL, "Entry": api.ENTRY_OUT, "Time": opened + 60, "Symbol": "XAUUSD", "Volume": 10000, "Profit": 631.90},
        ]
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=(deals, None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
            patch.object(api, "request_tick_history", return_value=([], None)),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        events = [event for event in result["events"] if event["detector"] == "starting_capital_high_profit"]
        summary = next(row for row in result["client_summaries"] if row["login"] == 1001)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["severity"], "high")
        self.assertEqual(events[0]["data_quality"], "PARTIAL")
        self.assertEqual(
            {event["detector"] for event in result["events"]} & {
                "low_deposit_high_profit",
                "lifetime_deposit_high_profit",
                "starting_capital_high_profit",
            },
            {"starting_capital_high_profit"},
        )
        self.assertEqual(summary["total_deposit"], 100.0)
        self.assertEqual(summary["lookback_deposit"], 100.0)
        self.assertEqual(summary["lifetime_deposits"], 100.0)
        self.assertEqual(summary["starting_balance"], 0.0)
        self.assertEqual(summary["starting_capital"], 100.0)
        self.assertEqual(summary["total_deals"], 1)
        self.assertEqual(summary["total_profit"], 631.9)
        self.assertIn("starting_capital_high_profit", summary["signals"])

    def test_low_deposit_high_profit_rejects_large_historical_funding(self):
        now = int(api.datetime.now(api.timezone.utc).timestamp())
        recent_deals = [
            {"Deal": 2, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": now - 300, "Profit": 50},
            {"Deal": 3, "PositionID": 77, "Login": 1001, "Action": api.DEAL_BUY, "Entry": 0, "Time": now - 240, "Symbol": "XAUUSD", "Volume": 10000},
            {"Deal": 4, "PositionID": 77, "Login": 1001, "Action": api.DEAL_SELL, "Entry": api.ENTRY_OUT, "Time": now - 60, "Symbol": "XAUUSD", "Volume": 10000, "Profit": 250},
        ]
        lifetime_deals = [
            {"Deal": 1, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": now - 172800, "Profit": 9000},
            *recent_deals,
        ]

        def deals_for_period(_api, _group, from_ts, _to_ts):
            return (lifetime_deals if from_ts == 0 else recent_deals), None

        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", side_effect=deals_for_period),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
            patch.object(api, "request_tick_history", return_value=([], None)),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        events = [event for event in result["events"] if event["detector"] == "low_deposit_high_profit"]
        summary = next(row for row in result["client_summaries"] if row["login"] == 1001)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["severity"], "warning")
        self.assertFalse(any(event["detector"] == "lifetime_deposit_high_profit" for event in result["events"]))
        self.assertFalse(any(event["detector"] == "starting_capital_high_profit" for event in result["events"]))
        self.assertEqual(summary["lookback_deposit"], 50.0)
        self.assertEqual(summary["lifetime_deposits"], 9050.0)
        self.assertEqual(summary["starting_balance"], 9000.0)
        self.assertEqual(summary["starting_capital"], 9050.0)

    def test_broker_funding_detectors_generate_independent_events(self):
        now = int(api.datetime.now(api.timezone.utc).timestamp())
        deals = [
            {"Deal": 1, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": now - 600, "Profit": 200},
            {"Deal": 2, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": now - 500, "Profit": 200},
            {"Deal": 3, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": now - 400, "Profit": 200},
            {"Deal": 4, "PositionID": 77, "Login": 1001, "Action": api.DEAL_BUY, "Entry": 0, "Time": now - 300, "Symbol": "XAUUSD", "Volume": 10000},
            {"Deal": 5, "PositionID": 77, "Login": 1001, "Action": api.DEAL_SELL, "Entry": api.ENTRY_OUT, "Time": now - 200, "Symbol": "XAUUSD", "Volume": 10000, "Profit": 600},
            {"Deal": 6, "PositionID": 78, "Login": 1001, "Action": api.DEAL_BUY, "Entry": 0, "Time": now - 180, "Symbol": "XAUUSD", "Volume": 10000},
            {"Deal": 7, "PositionID": 78, "Login": 1001, "Action": api.DEAL_SELL, "Entry": api.ENTRY_OUT, "Time": now - 120, "Symbol": "XAUUSD", "Volume": 10000, "Profit": 50},
            {"Deal": 8, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": now - 100, "Profit": -550},
        ]
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_deals_by_group", return_value=(deals, None)),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({}, [])),
            patch.object(api, "request_tick_history", return_value=([], None)),
        ):
            result = api.build_antifraud_snapshot(object(), ["live"])

        event_keys = {event["detector"] for event in result["events"]}
        self.assertTrue({
            "repeated_funding",
            "profit_withdrawal_pressure",
            "funding_churn",
        }.issubset(event_keys))
        detector_counts = {row["key"]: row["events"] for row in result["detectors"]}
        self.assertEqual(detector_counts["repeated_funding"], 1)
        self.assertEqual(detector_counts["profit_withdrawal_pressure"], 1)
        self.assertEqual(detector_counts["single_trade_profit_concentration"], 0)
        self.assertEqual(detector_counts["funding_churn"], 1)
        self.assertEqual(detector_counts["symbol_profit_concentration"], 0)

    def test_low_deposit_high_profit_telegram_contains_required_totals(self):
        summary = {
            "login": 1001, "name": "Client", "group": "live", "severity": "high",
            "signals": ["low_deposit_high_profit"], "total_deposit": 100,
            "lookback_deposit": 100, "lifetime_deposits": 100, "starting_capital": 100,
            "total_deals": 6, "total_profit": 631.90,
        }
        captured = []
        with patch.object(api, "TELEGRAM_ALERTS_ENABLED", True), patch.object(api, "TELEGRAM_ANTIFRAUD_ALERTS_ENABLED", True), patch.object(api, "_send_telegram_messages", side_effect=lambda messages: captured.extend(messages)):
            api.notify_new_antifraud_events([], [summary])

        self.assertEqual(len(captured), 1)
        self.assertIn("LOW DEPOSIT / HIGH PROFIT ALERT", captured[0])
        self.assertIn("Lookback Deposit: $100.00", captured[0])
        self.assertIn("Lifetime Deposits: $100.00", captured[0])
        self.assertIn("Starting Capital: $100.00", captured[0])
        self.assertIn("Total Deals: 6", captured[0])
        self.assertIn("Total Profit: $631.90", captured[0])


if __name__ == "__main__":
    unittest.main()
