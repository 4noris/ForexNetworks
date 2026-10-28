from __future__ import annotations

import unittest
import os
import tempfile
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import api


class ClientAnalyserTests(unittest.TestCase):
    def test_trade_journal_search_continues_past_login_only_rows(self):
        class Journal:
            def __init__(self):
                self.calls = []

            def LoggerServerRequest(self, mode, log_type, start, end, filter_text):
                self.calls.append(log_type)
                if log_type == 1:
                    return [{"Time": 1000, "Message": "account 1001 login from 8.8.8.8"}]
                return [{"Time": 1001, "Message": "deal #91001 account 1001 executed from 1.1.1.1"}]

        journal = Journal()
        enums = SimpleNamespace(
            EnMTLogRequestMode=SimpleNamespace(MTLogModeFull=1),
            EnMTLogType=SimpleNamespace(MTLogTypeLiveUpdate=1, MTLogTypeUser=2),
        )
        with patch.object(api, "MT5Manager", enums):
            rows, error = api.request_trade_journal(journal, 1001, 1, 2000, relevant_ids={91001})
        self.assertIsNone(error)
        self.assertIn(2, journal.calls)
        self.assertEqual(len(rows), 2)
        self.assertEqual(api.map_trade_execution_ips(
            [{"Deal": 91001, "PositionID": 71001, "Login": 1001}], rows, 1001,
        ), {71001: ["1.1.1.1"]})

    def test_configured_local_journal_maps_real_ticket_ip(self):
        timestamp = int(datetime(2026, 6, 20, tzinfo=timezone.utc).timestamp())
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "20260620.log").write_text(
                "trade deal #91001 for login 1001 executed from 8.8.8.8\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"MT5_SERVER_LOG_DIRS": directory}):
                rows, files = api.scan_local_trade_journals(1001, timestamp, timestamp + 86400, {91001})
        mapped = api.map_trade_execution_ips(
            [{"Deal": 91001, "PositionID": 71001, "Login": 1001}], rows, 1001
        )
        self.assertTrue(files)
        self.assertEqual(mapped, {71001: ["8.8.8.8"]})

    def test_journal_ip_maps_through_deal_ticket_to_position(self):
        deals = [{"Deal": 91001, "Order": 81001, "PositionID": 71001, "Login": 1001}]
        journals = [{"Time": 1000, "Message": "deal #91001 executed for account 1001 from 8.8.8.8"}]

        mapped = api.map_trade_execution_ips(deals, journals, 1001)

        self.assertEqual(mapped, {71001: ["8.8.8.8"]})

    def test_ticket_position_map_includes_deal_order_and_position_ids(self):
        mapping, positions = api.build_ticket_position_map(
            [{"Deal": 91001, "Order": 81001, "PositionID": 71001, "Login": 1001}], 1001
        )

        self.assertEqual(positions, {71001})
        self.assertEqual(mapping, {91001: 71001, 81001: 71001, 71001: 71001})

    def test_trade_ip_evidence_keeps_auditable_source(self):
        deals = [{"Deal": 91001, "PositionID": 71001, "Login": 1001}]
        journals = [{
            "Time": 1000,
            "Message": "deal #91001 executed for account 1001 from 8.8.8.8",
            "Source": "server-journal-01",
        }]

        evidence = api.map_trade_execution_evidence(deals, journals, 1001)

        self.assertEqual(evidence[71001]["ips"], ["8.8.8.8"])
        self.assertEqual(evidence[71001]["sources"], ["server-journal-01"])

    def test_trade_ip_changes_and_multi_ip_trades_are_chronological(self):
        trades = [
            {"position_id": 2, "open_ts": 200},
            {"position_id": 1, "open_ts": 100},
            {"position_id": 3, "open_ts": 300},
        ]
        evidence = {
            1: {"ips": ["8.8.8.8"], "sources": ["journal-a"]},
            2: {"ips": ["8.8.8.8", "1.1.1.1"], "sources": ["journal-a", "journal-b"]},
            3: {"ips": ["1.1.1.1"], "sources": ["journal-b"]},
        }
        network = {
            "8.8.8.8": {"provider": "Provider A", "vps_status": "not_detected"},
            "1.1.1.1": {"provider": "Provider B", "vps_status": "detected"},
        }

        stats = api.apply_trade_execution_evidence(trades, evidence, network)
        by_position = {row["position_id"]: row for row in trades}

        self.assertEqual(stats, {
            "covered_trades": 3,
            "ip_change_count": 1,
            "multi_ip_trades": 1,
            "inferred_trades": 0,
            "attributed_trades": 3,
        })
        self.assertFalse(by_position[1]["trade_ip_changed"])
        self.assertFalse(by_position[2]["trade_ip_changed"])
        self.assertTrue(by_position[3]["trade_ip_changed"])
        self.assertEqual(by_position[2]["trade_ip_sources"], ["journal-a", "journal-b"])
        self.assertEqual(by_position[2]["trade_ips"], ["8.8.8.8", "1.1.1.1"])

    def test_missing_execution_ip_uses_labelled_login_ip_fallback(self):
        trades = [{"position_id": 71001, "open_ts": 1000}]
        network = {"8.8.8.8": {"provider": "Provider A", "vps_status": "not_detected"}}

        stats = api.apply_trade_execution_evidence(
            trades, {}, network, fallback_ip="8.8.8.8"
        )

        self.assertEqual(stats["covered_trades"], 0)
        self.assertEqual(stats["inferred_trades"], 1)
        self.assertEqual(stats["attributed_trades"], 1)
        self.assertEqual(trades[0]["trade_ip"], "8.8.8.8")
        self.assertEqual(trades[0]["trade_ip_evidence"], "login_ip_fallback")
        self.assertEqual(trades[0]["trade_ip_sources"], ["account_last_login_inferred"])

    def test_journal_ip_does_not_infer_unrelated_login_ip(self):
        deals = [{"Deal": 91001, "PositionID": 71001, "Login": 1001}]
        journals = [{"Time": 1000, "Message": "account 1001 connected from 8.8.8.8"}]

        self.assertEqual(api.map_trade_execution_ips(deals, journals, 1001), {})

    def test_deals_are_collapsed_into_a_closed_position(self):
        deals = [
            {"Deal": 1, "PositionID": 77, "Login": 1001, "Action": 0, "Entry": 0, "Time": 1000, "Symbol": "EURUSD", "Volume": 10000, "Price": 1.1, "Commission": -2},
            {"Deal": 2, "PositionID": 77, "Login": 1001, "Action": 1, "Entry": 1, "Time": 1060, "Symbol": "EURUSD", "Volume": 10000, "Price": 1.2, "Profit": 25, "Commission": -2},
        ]

        rows = api.build_client_trade_lifecycles(deals, 1001)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["position_id"], 77)
        self.assertEqual(rows[0]["side"], "BUY")
        self.assertEqual(rows[0]["volume"], 1.0)
        self.assertEqual(rows[0]["hold_seconds"], 60)
        self.assertEqual(rows[0]["pnl"], 21.0)

    def test_pattern_engine_detects_reference_behaviours(self):
        def row(position, symbol, side, opened, closed, price, volume, pnl):
            return {
                "position_id": position, "symbol": symbol, "side": side,
                "open_ts": opened, "close_ts": closed,
                "open_time": "2026-01-01T00:00:00+00:00", "close_time": "2026-01-01T00:01:00+00:00",
                "open_price": price, "close_price": price, "volume": volume,
                "pnl": pnl, "hold_seconds": closed - opened,
            }

        rows = [
            row(1, "EURUSD", "BUY", 1000, 1010, 1.1000, 1.0, -10),
            row(2, "EURUSD", "SELL", 1020, 1030, 1.1002, 2.0, 5),
            row(3, "XAUUSD", "BUY", 2000, 2020, 2000.0, 1.0, 3),
            row(4, "XAUUSD", "BUY", 2100, 2120, 2003.0, 1.0, 2),
            row(5, "GBPUSD", "BUY", 3000, 3010, 1.25, 1.0, 1),
            row(6, "USDJPY", "BUY", 3001, 3011, 150.0, 1.0, 1),
        ]

        result = api.analyse_client_lifecycles(rows, scalping_seconds=180)

        self.assertEqual(result["patterns"]["scalp"]["count"], 6)
        self.assertEqual(result["patterns"]["reversal"]["count"], 1)
        self.assertEqual(result["patterns"]["burst"]["count"], 1)
        self.assertEqual(result["patterns"]["grid"]["count"], 1)
        self.assertEqual(result["patterns"]["martingale"]["count"], 1)
        self.assertEqual(result["metrics"]["toxicity_score"], 100.0)
        self.assertEqual(result["metrics"]["max_drawdown"], 10.0)


if __name__ == "__main__":
    unittest.main()
