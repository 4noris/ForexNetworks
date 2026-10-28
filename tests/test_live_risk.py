from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import api


class LiveRiskTests(unittest.TestCase):
    def test_live_cache_freshness_adapts_to_scope_size(self):
        with patch.object(api, "LIVE_RISK_CACHE_SECONDS", 60):
            self.assertEqual(api._live_risk_scope_cache_ttl(["live"]), 2)
            self.assertEqual(api._live_risk_scope_cache_ttl([str(i) for i in range(50)]), 10)
            self.assertEqual(api._live_risk_scope_cache_ttl(["live"], {"summary": {"scoped_accounts": 5000}}), 20)

    def test_user_scope_prefers_authoritative_request_over_empty_pump(self):
        class GroupUsersAPI:
            def UserRequestByGroup(self, group):
                self.requested_group = group
                return [{"Login": 1001, "Group": group, "Name": "Live client"}]

            def UserGetByGroup(self, _group):
                raise AssertionError("pump fallback must not run")

        manager = GroupUsersAPI()
        users, error = api.request_users_by_group(manager, "real\\live")

        self.assertIsNone(error)
        self.assertEqual(manager.requested_group, "real\\live")
        self.assertEqual(users[0]["Login"], 1001)

    def test_live_risk_prefers_bulk_account_requests_by_group(self):
        class BulkGroupAPI:
            def UserAccountGetByGroup(self, group):
                self.requested_group = group
                return [
                    {"Login": 1001, "Balance": 100, "Equity": 105},
                    {"Login": 2002, "Balance": 200, "Equity": 210},
                ]

        manager = BulkGroupAPI()
        with patch.object(
            api,
            "request_accounts_by_logins",
            side_effect=AssertionError("login fallback must not run"),
        ):
            accounts, warnings = api.request_accounts_by_groups(
                manager, ["real\\live"], {1001}
            )

        self.assertEqual(manager.requested_group, "real\\live")
        self.assertEqual(list(accounts), [1001])
        self.assertEqual(warnings, [])

    def test_account_login_bulk_tries_get_variant(self):
        class BulkLoginAPI:
            def UserAccountGetByLogins(self, logins):
                self.requested_logins = logins
                return [{"Login": login, "Equity": 10} for login in logins]

        manager = BulkLoginAPI()
        accounts, warnings = api.request_accounts_by_logins(manager, {1001, 1002})

        self.assertEqual(manager.requested_logins, [1001, 1002])
        self.assertEqual(sorted(accounts), [1001, 1002])
        self.assertEqual(warnings, [])

    def test_total_equity_includes_scoped_accounts_without_positions(self):
        users = {
            1001: {"name": "Active", "currency": "USD"},
            1002: {"name": "Inactive", "currency": "USD"},
        }
        positions = [{"Position": 1, "Login": 1001, "Symbol": "EURUSD", "Action": 0, "Volume": 10000, "Profit": 5, "TimeCreate": 1000}]
        accounts = {
            1001: {"Login": 1001, "Balance": 100, "Equity": 105, "Margin": 10, "MarginFree": 95},
            1002: {"Login": 1002, "Balance": 250, "Equity": 250, "Margin": 0, "MarginFree": 250},
        }
        with (
            patch.object(api, "_collect_user_scope", return_value=(users, {1001: "live", 1002: "live"}, [])),
            patch.object(api, "request_positions_by_group", return_value=(positions, None)),
            patch.object(api, "request_accounts_by_logins", return_value=(accounts, [])),
        ):
            result = api.build_live_risk_snapshot(object(), ["live"])

        self.assertEqual(result["summary"]["scoped_accounts"], 2)
        self.assertEqual(result["summary"]["active_accounts"], 1)
        self.assertEqual(result["summary"]["equity"], 355.0)
        self.assertEqual(result["summary"]["margin_free"], 345.0)

    def test_bulk_group_accounts_include_inactive_authoritative_equity(self):
        class Manager:
            def UserAccountGetByGroup(self, group):
                assert group == "live"
                return [
                    {"Login": 1001, "Equity": 105, "MarginFree": 95, "Profit": 5},
                    {"Login": 1002, "Equity": 275, "MarginFree": 275, "Profit": 0},
                ]

        users = {
            1001: {"name": "Active", "balance": 100, "credit": 0},
            1002: {"name": "Inactive", "balance": 250, "credit": 0},
        }
        positions = [{"Position": 1, "Login": 1001, "Symbol": "EURUSD", "Action": 0, "Volume": 10000, "Profit": 5}]
        with (
            patch.object(api, "_collect_user_scope", return_value=(users, {1001: "live", 1002: "live"}, [])),
            patch.object(api, "request_positions_by_group", return_value=(positions, None)),
        ):
            result = api.build_live_risk_snapshot(Manager(), ["live"])

        self.assertEqual(result["summary"]["equity"], 380.0)
        self.assertEqual(result["summary"]["authoritative_accounts"], 2)
        self.assertEqual(result["summary"]["estimated_accounts"], 0)

    def test_cache_portfolio_floating_includes_every_scoped_account(self):
        users = [
            {"Login": 1001, "Group": "live", "Name": "Active", "Balance": 100,
             "Equity": 112, "Live Floating PnL": 12},
            {"Login": 1002, "Group": "live", "Name": "No open position", "Balance": 100,
             "Equity": 103, "Floating PnL": 3},
        ]
        positions = [{"Login": 1001, "Ticket": 1, "Symbol": "EURUSD", "Action": 0,
                      "Volume": 1, "Profit": 12, "Swap": 0}]

        result = api.build_live_risk_snapshot_from_cache(users, positions, ["live"])

        self.assertEqual(result["summary"]["floating_pnl"], 15.0)
        self.assertEqual(result["summary"]["active_accounts"], 1)

    def test_ip_intelligence_requires_provider_evidence_for_vps(self):
        hosted = api._normalize_ip_intel("8.8.8.8", {
            "status": "success", "country": "United States", "city": "Ashburn",
            "lat": 39.0, "lon": -77.5, "isp": "Google LLC", "as": "AS15169 Google LLC",
            "hosting": True, "proxy": False,
        })
        residential = api._normalize_ip_intel("1.2.3.4", {
            "status": "success", "country": "India", "city": "Mumbai",
            "lat": 19.0, "lon": 72.8, "isp": "Residential Broadband", "as": "AS1234 ISP",
            "hosting": False, "proxy": False,
        })

        self.assertEqual(hosted["vps_status"], "detected")
        self.assertGreaterEqual(hosted["vps_confidence"], 90)
        self.assertEqual(residential["vps_status"], "not_detected")
        self.assertEqual(residential["vps_confidence"], 0)

    def test_snapshot_flags_margin_loss_grid_and_volume_escalation(self):
        now = int(datetime.now(timezone.utc).timestamp())
        positions = [
            {"Position": 1, "Login": 1001, "Symbol": "XAUUSD", "Action": 0, "Volume": 10000, "Profit": -100, "TimeCreate": now - 6},
            {"Position": 2, "Login": 1001, "Symbol": "XAUUSD", "Action": 0, "Volume": 20000, "Profit": -150, "TimeCreate": now - 4},
            {"Position": 3, "Login": 1001, "Symbol": "XAUUSD", "Action": 1, "Volume": 40000, "Profit": -200, "TimeCreate": now - 2},
        ]
        account = {
            "Login": 1001,
            "Balance": 1000,
            "Credit": 0,
            "Equity": 550,
            "Margin": 500,
            "MarginFree": 50,
            "MarginLevel": 90,
            "Profit": -450,
        }

        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Risk Client", "currency": "USD"}}, {1001: "live"}, [])),
            patch.object(api, "request_positions_by_group", return_value=(positions, None)),
            patch.object(api, "request_accounts_by_logins", return_value=({1001: account}, [])),
        ):
            snapshot = api.build_live_risk_snapshot(object(), ["live"])

        row = snapshot["accounts"][0]
        self.assertEqual(row["severity"], "critical")
        self.assertEqual(row["risk_score"], 100)
        self.assertIn("Stop-out proximity", row["signals"])
        self.assertIn("Grid-like exposure", row["signals"])
        self.assertIn("Volume escalation", row["signals"])
        self.assertIn("Burst execution", row["signals"])
        self.assertIn("Opposing symbol exposure", row["signals"])
        self.assertEqual(snapshot["summary"]["floating_pnl"], -450.0)
        self.assertEqual(snapshot["symbols"][0]["gross_lots"], 7.0)


if __name__ == "__main__":
    unittest.main()
