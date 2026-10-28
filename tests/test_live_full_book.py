import time
from types import SimpleNamespace

import api
import mt5_connection
from services.live_scope import scope_live_payload
from services.live_stream import frame


def source():
    users = [{"Login": login, "Name": f"Fixture {login}", "Group": "fixture", "Currency": "USD",
              "Balance": 100, "Equity": 110, "Margin": 5, "Free Margin": 105,
              "Margin Level %": 2200, "Live Floating PnL": 10} for login in range(1, 1001)]
    positions = [{"Login": 1 + index % 579, "Symbol": f"S{index % 120}",
                  "Ticket": str(18446744073709500000 + index), "Action": index % 2,
                  "Volume": 1, "Profit": index / 100, "Swap": 0, "PriceOpen": 100,
                  "PriceCurrent": 101} for index in range(4817)]
    return users, positions


def test_default_cache_view_returns_every_active_account_position_and_symbol(monkeypatch):
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    users, positions = source()
    payload = api.build_live_risk_snapshot_from_cache(users, positions, ["fixture"])
    assert len(payload["accounts"]) == 579
    assert len(payload["positions"]) == 4817
    assert len(payload["symbols"]) == 120
    assert len(payload["large_positions"]) == 4817
    assert payload["summary"]["currency_totals"]["USD"]["accounts"] == 1000
    assert payload["summary"]["equity"] == 110000
    assert all(isinstance(row["ticket"], str) for row in payload["positions"])
    assert not any("Showing" in warning for warning in payload["warnings"])


def test_filter_step_does_not_reintroduce_any_detail_cap(monkeypatch):
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    users, positions = source()
    payload = api.build_live_risk_snapshot_from_cache(users, positions, ["fixture"])
    for row in payload["accounts"]:
        row["severity"] = "elevated"
    scoped = scope_live_payload(payload, risk_category="elevated")
    assert len(scoped["accounts"]) == 579
    assert len(scoped["positions"]) == 4817
    assert len(scoped["large_positions"]) == 4817
    assert len(scoped["symbols"]) == 120
    assert scoped["summary"]["net_lots"] == 1
    assert scoped["summary"]["open_positions"] == 4817


def test_live_endpoint_uses_complete_cache_and_keeps_row_order_stable(monkeypatch):
    users, positions = source()
    view = {"users": users, "positions": positions, "groups": ["fixture"], "last_update": time.time(), "errors": []}
    manager = SimpleNamespace(is_connected=True, server="fixture", manager_login=1,
                              live_risk_cache_view=lambda **kwargs: view)
    monkeypatch.setattr(api, "get_configured_manager", lambda: manager)
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    monkeypatch.setattr(api, "notify_live_risk_events", lambda *args: None)
    monkeypatch.setattr(api, "_apply_live_risk_notices", lambda payload: payload)
    first = api.get_live_risk(api.LiveRiskRequest(selected_groups=["fixture"]))
    assert len(first["positions"]) == 4817
    assert len(first["accounts"]) == 579
    positions[-1] = {**positions[-1], "Profit": -9999}
    second = api.get_live_risk(api.LiveRiskRequest(selected_groups=["fixture"]))
    assert [row["ticket"] for row in first["positions"]] == [row["ticket"] for row in second["positions"]]
    assert second["positions"][-1]["profit"] == -9999
    assert [row["login"] for row in first["accounts"]] == [row["login"] for row in second["accounts"]]
    delta = frame(first, second, 1)
    assert delta["type"] == "delta"


def test_inactive_accounts_keep_money_totals_without_network_profile_work(monkeypatch):
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    calls = []
    monkeypatch.setattr(api, "_ip_text", lambda ip: calls.append(ip) or "")
    users, positions = source()
    payload = api.build_live_risk_snapshot_from_cache(users, positions[:2], ["fixture"])
    assert len(calls) == 2
    assert len(payload["accounts"]) == 2
    assert payload["summary"]["equity"] == 110000


def test_metadata_catalog_read_does_not_walk_account_profiles():
    class Unreadable:
        def __iter__(self):
            raise AssertionError("Metadata reads must not walk the account catalog")
    manager = mt5_connection.MT5ConnectionManager("fixture", 1, "")
    manager.cache.update(users=Unreadable(), positions=[], groups=["fixture"])
    assert manager.live_risk_cache_view(groups=[])["groups"] == ["fixture"]


def test_explicit_fallback_builder_also_has_no_default_truncation(monkeypatch):
    count = 601
    users = {login: {"name": "fixture", "currency": "USD", "balance": 100} for login in range(1, count + 1)}
    monkeypatch.setattr(api, "_collect_user_scope", lambda *a, **kw: (users, {login: "fixture" for login in users}, []))
    positions = [{"Login": login, "Position": 18446744073709500000 + login, "Symbol": "TEST", "Action": 0,
                  "Volume": 10000, "Profit": 1} for login in users]
    accounts = {login: {"Login": login, "Equity": 101, "Balance": 100, "Margin": 1, "MarginFree": 100} for login in users}
    monkeypatch.setattr(api, "request_positions_by_group", lambda *a: (positions, None))
    monkeypatch.setattr(api, "request_accounts_by_logins", lambda *a: (accounts, []))
    monkeypatch.setattr(api, "enrich_ip_addresses_nonblocking", lambda ips: {})
    result = api.build_live_risk_snapshot(object(), ["fixture"])
    assert len(result["accounts"]) == count
    assert len(result["positions"]) == count
    assert isinstance(result["positions"][-1]["ticket"], str)
