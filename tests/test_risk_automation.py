from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from risk_automation import RiskAutomationService


class FakeUser:
    def __init__(self) -> None:
        self.Login = 1001
        self.Group = "Standard"
        self.Rights = 1
        self.Leverage = 100


class FakeManager:
    is_connected = True

    def __init__(self) -> None:
        self.user = FakeUser()
        self.updated = False
        self.data = SimpleNamespace(
            users=[{
                "Login": 1001, "Name": "Test Client", "Group": "Standard",
                "Balance": 1000, "Equity": 1250, "Leverage": 100,
                "WinRate": 85, "ToxicityScore": 72,
            }],
            positions=[
                {"Login": 1001, "Symbol": "EURUSD", "Action": 0, "Volume": 10000, "Profit": 200},
                {"Login": 1001, "Symbol": "EURUSD", "Action": 1, "Volume": 5000, "Profit": 50},
            ],
            last_update=123.0,
        )

    def snapshot(self):
        return self.data

    def call(self, method, *args, **kwargs):
        if method == "UserGet":
            return self.user
        if method == "UserUpdate":
            self.updated = True
            return True
        raise AssertionError(method)


class RejectingManager(FakeManager):
    def call(self, method, *args, **kwargs):
        if method == "UserUpdate":
            self.updated = True
            return False
        return super().call(method, *args, **kwargs)


class RiskAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = FakeManager()
        self.audit = []
        self.service = RiskAutomationService(
            str(Path(self.temp.name) / "risk.db"), lambda: self.manager,
            lambda *args: self.audit.append(args), lambda message: (True, "sent"),
        )
        self.service.ensure_schema()
        self.payload = {
            "name": "High win rate", "description": "test", "enabled": True,
            "priority": 10, "source_group": "Standard", "target_group": "Risk",
            "condition_logic": "AND", "delay_minutes": 0, "require_approval": False,
            "revert_when_clear": True, "simulation_mode": True, "cooldown_minutes": 60,
            "conditions": [
                {"type": "high_win_rate", "field": "win_rate", "operator": "gt", "value": 80},
                {"type": "abnormal_profitability", "field": "profit_pct", "operator": "gte", "value": 20},
            ],
            "actions": [{"type": "move_group"}, {"type": "monitoring_list"}],
            "alert_events": [],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_rule_round_trip_and_simulation(self):
        rule = self.service.save_rule(self.payload, "admin")
        result = self.service.simulate(rule)
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["clients"][0]["detected"]["logic"], "AND")
        self.assertEqual(self.service.overview()["summary"]["rules"], 1)

    def test_excluded_account_is_skipped_before_evaluation(self):
        rule = self.service.save_rule(self.payload | {"excluded_logins": [1001]}, "admin")
        result = self.service.run(rule["id"], "admin")
        self.assertEqual(rule["excluded_logins"], [1001])
        self.assertEqual(result["evaluated"], 0)
        self.assertEqual(self.service.overview()["history"], [])

    def test_execute_moves_group_and_persists_internal_control(self):
        rule = self.service.save_rule(self.payload | {"simulation_mode": False}, "admin")
        result = self.service.run(rule["id"], "admin")
        self.assertEqual(result["executed"], 1)
        self.assertTrue(self.manager.updated)
        self.assertEqual(self.manager.user.Group, "Risk")
        overview = self.service.overview()
        self.assertTrue(overview["controls"][0]["monitoring"])
        self.assertEqual(overview["history"][0]["status"], "success")
        self.assertEqual(overview["history"][0]["account_equity"], 1250)

    def test_internal_controls_are_skipped_when_mt5_update_fails(self):
        self.manager = RejectingManager()
        rule = self.service.save_rule(self.payload | {"simulation_mode": False}, "admin")

        result = self.service.run(rule["id"], "admin")

        self.assertEqual(result["failed"], 1)
        self.assertTrue(self.manager.updated)
        overview = self.service.overview()
        self.assertEqual(overview["controls"], [])
        self.assertEqual(overview["history"][0]["status"], "failed")

    def test_promoting_simulation_executes_existing_match(self):
        rule = self.service.save_rule(self.payload, "admin")
        simulated = self.service.run(rule["id"], "admin")
        self.assertEqual(simulated["simulated"], 1)
        self.assertFalse(self.manager.updated)

        self.service.save_rule(self.payload | {"simulation_mode": False}, "admin", rule["id"])
        live = self.service.run(rule["id"], "admin")

        self.assertEqual(live["executed"], 1)
        self.assertTrue(self.manager.updated)
        self.assertEqual(self.manager.user.Group, "Risk")

    def test_approval_prevents_mt5_change_until_approved(self):
        rule = self.service.save_rule(
            self.payload | {"simulation_mode": False, "require_approval": True}, "admin"
        )
        result = self.service.run(rule["id"], "admin")
        self.assertEqual(result["approvals"], 1)
        self.assertFalse(self.manager.updated)
        approval = self.service.overview()["approvals"][0]
        self.service.decide_approval(approval["id"], "approved", "admin", "verified")
        self.assertTrue(self.manager.updated)
        self.assertEqual(self.manager.user.Group, "Risk")


if __name__ == "__main__":
    unittest.main()
