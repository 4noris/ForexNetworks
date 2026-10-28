from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class FakeManager:
    def SymbolGet(self, symbol):
        return {"ContractSize": 10}

    def GroupGet(self, group):
        return {"Currency": "USD", "MarginCall": 120, "MarginStopout": 50, "LeverageDefault": 100}


class RiskControlTests(unittest.TestCase):
    def test_client_live_snapshot_calculates_long_short_and_net_exposure(self):
        positions = [
            {"Position": 1, "Login": 1001, "Symbol": "TEST", "Action": 0, "Volume": 20000, "PriceCurrent": 100, "Profit": 10},
            {"Position": 2, "Login": 1001, "Symbol": "TEST", "Action": 1, "Volume": 10000, "PriceCurrent": 100, "Profit": -4},
        ]
        account = {"Balance": 500, "Equity": 506, "Margin": 100, "MarginFree": 406, "MarginLevel": 506}
        with (
            patch.object(api, "request_positions_by_logins", return_value=(positions, None)),
            patch.object(api, "request_account", return_value=(account, None)),
        ):
            result = api.build_client_live_snapshot(FakeManager(), 1001, {"Name": "Client", "Group": "live"})
        summary = result["summary"]
        self.assertEqual(summary["long_lots"], 2.0)
        self.assertEqual(summary["short_lots"], 1.0)
        self.assertEqual(summary["net_lots"], 1.0)
        self.assertEqual(summary["long_exposure"], 2000.0)
        self.assertEqual(summary["short_exposure"], 1000.0)
        self.assertEqual(summary["net_exposure"], 1000.0)
        self.assertEqual(summary["floating_pnl"], 6.0)

    def test_stress_uses_real_position_notional_and_account_equity(self):
        positions = [{
            "Position": 77, "Login": 1001, "Symbol": "TEST", "Action": 0,
            "Volume": 10000, "PriceCurrent": 100, "Profit": 5,
        }]
        account = {"Login": 1001, "Balance": 500, "Equity": 500, "Margin": 100, "MarginLevel": 500}
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_positions_by_group", return_value=(positions, None)),
            patch.object(api, "request_accounts_by_logins", return_value=({1001: account}, [])),
        ):
            result = api.build_stress_snapshot(
                FakeManager(), ["live"], shock_percent=-10,
                margin_level_floor=450, concentration_limit=70,
                symbol_gross_limit=900,
            )

        self.assertEqual(result["summary"]["equity"], 500.0)
        self.assertEqual(result["summary"]["gross_exposure"], 1000.0)
        self.assertEqual(result["summary"]["shock_pnl"], -100.0)
        self.assertEqual(result["summary"]["projected_equity"], 400.0)
        self.assertEqual(result["summary"]["account_breaches"], 1)
        self.assertEqual(result["summary"]["symbol_breaches"], 1)
        self.assertTrue(result["group_policies"][0]["available"])

    def test_stress_excludes_accounts_without_authoritative_equity(self):
        account = {"Login": 1001, "Balance": 500, "Margin": 100, "MarginLevel": 500}
        with (
            patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Client"}}, {1001: "live"}, [])),
            patch.object(api, "request_positions_by_group", return_value=([], None)),
            patch.object(api, "request_accounts_by_logins", return_value=({1001: account}, [])),
        ):
            result = api.build_stress_snapshot(FakeManager(), ["live"])

        self.assertEqual(result["summary"]["equity"], 0.0)
        self.assertEqual(result["accounts"], [])
        self.assertTrue(any("authoritative equity is unavailable" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
