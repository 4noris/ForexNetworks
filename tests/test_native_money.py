import math
import random

from mt5_connection import MT5ConnectionManager
from services.live_money import currency_money_totals, NATIVE_FIELDS
from services.native_money import NativeGroupMoney, portfolio_summary
from services.native_bridge import NativeProjection
from test_native_bridge import snapshot
from test_native_changes import change_frame


def row(login, group="A", currency="USD", equity=110):
    return {"Login": login, "Name": "fixture", "Group": group, "Currency": currency,
            "Balance": 100, "Equity": equity, "Margin": 5, "Free Margin": equity - 5,
            "Floating PnL": equity - 100, "Live Floating PnL": equity - 100,
            "Margin Level %": 2200, "Enabled": True}


def test_exact_group_totals_match_full_reads_across_changes_moves_and_deletions():
    rng = random.Random(71)
    users = {login: row(login, "A" if login % 2 else "B", "USD" if login % 3 else "USC") for login in range(1, 301)}
    groups = NativeGroupMoney()
    for user in users.values():
        groups.replace(None, user)
    for cycle in range(20):
        for login in rng.sample(range(1, 301), 20):
            old = users.get(login)
            new = None if cycle % 5 == 0 else row(login, rng.choice(["A", "B"]), rng.choice(["USD", "USC", ""]), round(rng.uniform(1, 10000), 2))
            groups.replace(old, new)
            if new is None:
                users.pop(login, None)
            else:
                users[login] = new
        actual = portfolio_summary(groups.snapshot(), {"A", "B"}, {})
        assert actual["currency_totals"] == currency_money_totals(list(users.values()), native=True)
        assert actual["scoped_accounts"] == len(users)
        for name, field in NATIVE_FIELDS.items():
            assert actual[name] == round(math.fsum(user[field] for user in users.values()), 2)


def test_currency_changes_exclusions_and_immutable_previous_partials():
    groups = NativeGroupMoney()
    first, second, third = row(1), row(2, equity=1e16), row(3, equity=-1e16)
    for user in (first, second, third):
        groups.replace(None, user)
    previous = groups.snapshot()
    assert portfolio_summary(previous, {"A"}, {})["equity"] == 110
    assert portfolio_summary(previous, {"A"}, {"A": "USC"}, [second, third])["currency_totals"] == {
        "USC": {"accounts": 1, "balance": 100, "equity": 110, "margin": 5, "margin_free": 105, "floating_pnl": 10}}
    groups.replace(first, row(1, equity=200))
    assert portfolio_summary(previous, {"A"}, {})["equity"] == 110
    assert portfolio_summary(groups.snapshot(), {"A"}, {})["equity"] == 200


def test_fast_risk_view_never_iterates_inactive_profiles_and_retains_full_money():
    users = [row(login) for login in range(1, 10001)]
    class NoIteration(list):
        def __iter__(self):
            raise AssertionError("Fast live views must not scan inactive users")
    money = NativeGroupMoney()
    for user in users:
        money.replace(None, user)
    manager = MT5ConnectionManager("fixture", 1, "")
    manager.native_mode = True
    manager.cache.update(users=NoIteration(users), users_by_login={r["Login"]: r for r in users},
        positions=[{"Login": 1, "Ticket": "1", "Symbol": "TEST", "Volume": .01}],
        groups=["A"], group_currencies={"A": "USD"}, native_money_totals=money.snapshot())
    view = manager.live_risk_cache_view(groups=["A"], active_only=True, excluded_logins=[2], include_login=3)
    assert [user["Login"] for user in view["users"]] == [1, 3]
    assert view["portfolio_summary"]["scoped_accounts"] == 9999
    assert view["portfolio_summary"]["currency_totals"]["USD"]["equity"] == 1099890


def test_projection_publishes_money_with_the_matching_immutable_generation():
    manager = MT5ConnectionManager("fixture", 1, "")
    state = NativeProjection()
    state.snapshot(snapshot())
    first = state.capture()
    first.publish(manager)
    previous = manager.cache["native_money_totals"]
    state.apply_changes(change_frame(state))
    second = state.capture(first)
    second.publish(manager)
    assert portfolio_summary(previous, {"real"}, {"real": "USD"})["equity"] == 127
    assert portfolio_summary(manager.cache["native_money_totals"], {"real"}, {"real": "USD"})["equity"] == 321


def test_api_fast_view_keeps_inactive_accounts_in_complete_portfolio_totals(monkeypatch):
    import time
    import api
    import mt5_connection
    users = [row(login) for login in range(1, 101)]
    money = NativeGroupMoney()
    for user in users:
        money.replace(None, user)
    manager = MT5ConnectionManager("fixture", 1, "")
    manager.native_mode = True
    manager.api = object()
    manager.cache.update(users=users, users_by_login={r["Login"]: r for r in users},
        positions=[{"Login": 1, "Ticket": "1", "Symbol": "TEST", "Action": 0, "Volume": .01}],
        groups=["A"], group_currencies={"A": "USD"}, native_money_totals=money.snapshot(),
        last_update=time.time(), status="connected", errors=[])
    monkeypatch.setattr(api, "get_configured_manager", lambda: manager)
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    monkeypatch.setattr(api, "notify_live_risk_events", lambda *args: None)
    monkeypatch.setattr(api, "_apply_live_risk_notices", lambda payload: payload)
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    result = api.get_live_risk(api.LiveRiskRequest(selected_groups=["A"], excluded_logins=[2]))
    assert len(result["accounts"]) == 1
    assert result["summary"]["scoped_accounts"] == 99
    assert result["summary"]["currency_totals"]["USD"]["accounts"] == 99
    assert result["summary"]["equity"] == 10890
    filtered = api.get_live_risk(api.LiveRiskRequest(selected_groups=["A"], account_login=3))
    assert len(filtered["accounts"]) == 1
    assert filtered["accounts"][0]["login"] == 3
    assert filtered["summary"]["scoped_accounts"] == 1
    assert filtered["summary"]["equity"] == 110
