from __future__ import annotations

from datetime import date
from unittest.mock import patch

import api


def test_deal_dashboard_separates_mt5_profit_from_posted_charges():
    deals = api.build_deals_dataframe([{
        "Login": 1, "Name": "Client", "Group": "real", "Symbol": "EURUSD",
        "Volume": 1, "Time": "2026-10-03T12:00:00+00:00",
        "Profit": 100, "Commission": -5, "Fee": -2, "Swap": -3, "PnL": 90,
    }])
    report = api.compute_client_report(deals)
    kpis = api.compute_kpis(report)
    assert kpis["trade_profit"] == 100
    assert kpis["total_pnl"] == 90
from mt5_connection import MT5ConnectionManager


def test_account_cache_prefers_one_bulk_group_request():
    class BulkAccountsAPI:
        def __init__(self):
            self.calls = 0

        def UserAccountGetByGroup(self, group):
            self.calls += 1
            assert group == "*"
            return [{"Login": 1001, "Balance": 100.0}, {"Login": 1002, "Balance": 200.0}]

        def UserAccountGet(self, _login):
            raise AssertionError("per-account fallback must not be used")

    manager = MT5ConnectionManager("server", 1, "secret")
    manager.api = BulkAccountsAPI()

    accounts = manager.fetch_accounts_by_logins([1001, 1002], strict=True)

    assert sorted(accounts) == [1001, 1002]
    assert manager.api.calls == 1


def test_live_risk_uses_authoritative_account_equity_and_profit():
    users = [{
        "Login": 1001,
        "Name": "Client",
        "Group": "real",
        "Balance": 1000.0,
        "Credit": 50.0,
        "Equity": 0.0,
        "Margin": 20.0,
        "Free Margin": -20.0,
        "Margin Level %": 0.0,
        "Floating PnL": 25.0,
    }]
    positions = [{
        "Login": 1001,
        "Ticket": 10,
        "Symbol": "EURUSD",
        "Action": 0,
        "Volume": 1.0,
        "Profit": 99.0,
        "Swap": 1.0,
    }]

    with patch.object(api, "enrich_ip_addresses", return_value={}):
        snapshot = api.build_live_risk_snapshot_from_cache(users, positions, ["real"])

    assert snapshot["summary"]["equity"] == 0.0
    assert snapshot["summary"]["floating_pnl"] == 25.0
    assert snapshot["accounts"][0]["margin_free"] == -20.0


def test_pnl_includes_manager_fee_in_net_result():
    deal = {
        "Login": 1001,
        "Action": api.DEAL_BUY,
        "Entry": api.ENTRY_OUT,
        "Time": 1,
        "Volume": 10000,
        "Profit": 100.0,
        "Commission": -2.0,
        "Fee": -1.0,
        "Swap": -3.0,
    }
    with (
        patch.object(api, "request_users_by_group", return_value=([{"Login": 1001, "Group": "real"}], None)),
        patch.object(api, "request_deals_by_group", return_value=([deal], None)),
    ):
        deals, *_ = api.build_deal_report(object(), ["real"], date(2026, 7, 1), date(2026, 7, 1))

    assert deals.iloc[0]["PnL"] == 94.0


def test_complete_group_branch_is_compressed_to_one_manager_request():
    available = [r"real\BANK\A", r"real\BANK\B", r"real\OTHER\A"]

    assert api._compress_complete_group_branches(available[:2], available) == [
        r"real\BANK\*"
    ]
    assert api._compress_complete_group_branches([available[0]], available) == [
        available[0]
    ]


def test_wildcard_user_scope_rejects_unselected_neighbor_groups():
    rows = [
        {"Login": 1, "Group": r"real\BANK\A", "Name": "Selected"},
        {"Login": 2, "Group": r"real\BANK\C", "Name": "Neighbor"},
    ]
    with patch.object(api, "request_users_by_group", return_value=(rows, None)):
        users, group_map, warnings = api._collect_user_scope(
            object(),
            [r"real\BANK\A", r"real\BANK\B"],
            request_groups=[r"real\BANK\*"],
        )

    assert set(users) == {1}
    assert group_map == {1: r"real\BANK\A"}
    assert warnings == []
