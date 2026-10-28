import math

import api
from services.live_scope import scope_live_payload


def user(login=1):
    return {"Login": login, "Group": "fixture", "Currency": "USD", "Balance": 100,
            "Credit": 0, "Equity": 110, "Margin": 5, "Free Margin": 105,
            "Margin Level %": 2200, "Live Floating PnL": 10}


def positions():
    return [{"Login": 1, "Ticket": str(index + 1), "Symbol": "TEST", "Action": action,
             "Volume": volume, "PriceOpen": .00000001, "PriceCurrent": .00000002,
             "Profit": 1, "Swap": 0}
            for index, (action, volume) in enumerate([(0, .001), (0, .007), (1, .002), (0, 1.23456789)])]


def test_complete_and_filtered_live_book_preserve_fractional_lots_and_prices(monkeypatch):
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    result = api.build_live_risk_snapshot_from_cache([user()], positions(), ["fixture"])
    summary = result["summary"]
    assert summary["buy_lots"] == 1.24256789
    assert summary["sell_lots"] == .002
    assert summary["gross_lots"] == 1.24456789
    assert summary["net_lots"] == 1.24056789
    assert result["accounts"][0]["gross_lots"] == 1.24456789
    assert result["symbols"][0]["net_lots"] == 1.24056789
    assert sorted(row["volume"] for row in result["positions"]) == [.001, .002, .007, 1.23456789]
    assert result["positions"][0]["price_open"] == .00000001
    filtered = scope_live_payload(result, symbol="TEST")
    assert math.isclose(filtered["summary"]["net_lots"], 1.24056789, abs_tol=1e-12)


def sdk_report(monkeypatch, accounts):
    users = {1: {"name": "fixture", "currency": "USD", "balance": 100, "credit": 20},
             2: {"name": "fixture", "currency": "USD", "balance": 200, "credit": 40}}
    monkeypatch.setattr(api, "_collect_user_scope", lambda *args, **kwargs: (users, {1:"fixture", 2:"fixture"}, []))
    monkeypatch.setattr(api, "request_positions_by_group", lambda *args: ([{
        "Position": 1, "Login": 1, "Symbol": "TEST", "Action": 0, "VolumeExt": 100000,
        "Profit": 10, "Swap": 0, "PriceOpen": .00000001, "PriceCurrent": .00000002}], None))
    monkeypatch.setattr(api, "request_accounts_by_logins", lambda *args: (accounts, []))
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    return api.build_live_risk_snapshot(object(), ["fixture"])


def test_sdk_fractional_volume_and_prices_keep_source_precision(monkeypatch):
    account = {"Login": 1, "Balance": 100, "Equity": 110, "Margin": 5, "MarginFree": 105, "Profit": 10}
    result = sdk_report(monkeypatch, {1: account, 2: dict(account, Login=2)})
    assert result["summary"]["gross_lots"] == .001
    assert result["summary"]["buy_lots"] == .001
    assert result["positions"][0]["volume"] == .001
    assert result["positions"][0]["price_open"] == .00000001


def test_missing_account_does_not_invent_equity_or_zero_margin(monkeypatch):
    result = sdk_report(monkeypatch, {1: {"Login": 1, "Balance": 100, "Equity": 110, "Margin": 5, "MarginFree": 105, "Profit": 10}})
    assert result["summary"]["estimated_accounts"] == 1
    for field in ("equity", "margin", "margin_free", "floating_pnl", "balance"):
        assert result["summary"][field] is None


def test_missing_account_fields_are_unavailable_not_zero(monkeypatch):
    result = sdk_report(monkeypatch, {1: {"Login": 1, "Equity": 110}, 2: {"Login": 2, "Equity": 200}})
    assert result["summary"]["equity"] == 310
    assert result["summary"]["margin"] is None
    assert result["summary"]["floating_pnl"] is None
    assert result["accounts"][0]["margin"] is None
    assert result["accounts"][0]["floating_pnl"] is None


def test_command_center_uses_the_same_precise_native_lots(monkeypatch, tmp_path):
    from test_dealer_command_center import FakeManager
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "command.db")
    api.init_db()
    manager = FakeManager([dict(user(), Group="live")], positions())
    monkeypatch.setattr(api, "get_configured_manager", lambda: manager)
    monkeypatch.setattr(api.time_module, "time", lambda: 1_800_000_001)
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    result = api.dealer_command_center(api.GroupScopedRequest(selected_groups=["live"]))
    assert result["kpis"]["gross_lots"] == 1.24456789
    assert result["kpis"]["net_lots"] == 1.24056789
    assert result["symbol_exposure"][0]["buy_lots"] == 1.24256789


def test_manager_totals_never_combine_usd_and_cent_deposit_units(monkeypatch):
    from types import SimpleNamespace
    import time
    rows = [dict(user(1), Group="dollar"), dict(user(2), Group="cent", Balance=10000, Equity=11000)]
    for row in rows:
        row.pop("Currency")
    view = {"users": rows, "positions_count": 0, "metrics": {}, "last_update": time.time(),
            "status":"connected", "errors":[], "summary_rows":[], "exposure_rows":[],
            "group_currencies":{"dollar":"USD", "cent":"USC"}}
    manager = SimpleNamespace(is_connected=True, server="fixture", manager_login=1, dashboard_cache_view=lambda:view)
    monkeypatch.setattr(api, "_get_manager_from_request", lambda request: manager)
    result = api.manager_dashboard(None)
    assert result["totals"]["visible_accounts"] == 2
    assert result["totals"]["total_equity"] is None
    assert result["totals"]["total_balance"] is None
    assert result["totals"]["currency_totals"]["USD"]["equity"] == 110
    assert result["totals"]["currency_totals"]["USC"]["equity"] == 11000
    assert [row["currency"] for row in result["users"]] == ["USD", "USC"]
    rows[0]["Equity"] = None
    assert api.manager_dashboard(None)["totals"]["currency_totals"]["USD"]["equity"] is None


def test_manager_uses_matching_native_partials_without_resumming_all_accounts(monkeypatch):
    from types import SimpleNamespace
    import time
    from services.native_money import NativeGroupMoney
    rows = [user(1), dict(user(2), Group="cent", Currency="USC", Balance=10000, Equity=11000)]
    groups = NativeGroupMoney()
    for row in rows:
        groups.replace(None, row)
    view = {"users":rows, "positions_count":0, "metrics":{}, "last_update":time.time(), "status":"connected",
            "errors":[], "summary_rows":[], "exposure_rows":[], "native_money_totals":groups.snapshot()}
    manager = SimpleNamespace(is_connected=True, server="fixture", manager_login=1, dashboard_cache_view=lambda:view)
    monkeypatch.setattr(api, "_get_manager_from_request", lambda request: manager)
    monkeypatch.setattr(api, "currency_money_totals", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Native partials must avoid a second full financial scan")))
    result = api.manager_dashboard(None)
    assert result["totals"]["currency_totals"]["USD"]["balance"] == 100
    assert result["totals"]["currency_totals"]["USC"]["balance"] == 10000
