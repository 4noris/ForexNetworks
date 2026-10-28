from types import SimpleNamespace

import api


class FakeManager:
    def __init__(self, users, positions):
        self.users = users
        self.positions = positions

    def live_risk_cache_view(self, groups=None):
        if groups == []:
            return {"groups": ["live"], "last_update": 1_800_000_000, "users": [], "positions": []}
        return {"groups": ["live"], "last_update": 1_800_000_000, "users": self.users, "positions": self.positions}


def test_command_center_uses_real_scope_and_marks_missing_notional(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "command-center.db")
    api.init_db()
    user = {"Login": 1001, "Group": "live", "Name": "Trader", "Currency": "USD", "Balance": 1000, "Credit": 200, "Equity": 1100, "Margin": 100, "Free Margin": 1000, "Floating PnL": 100}
    position = {"Login": 1001, "Symbol": "EURUSD", "Action": 0, "Volume": 1.0, "PriceCurrent": 1.1, "ContractSize": 100000, "Profit": 100, "Swap": 0, "Ticket": 1}
    monkeypatch.setattr(api, "get_configured_manager", lambda: FakeManager([user], [position]))
    monkeypatch.setattr(api.time_module, "time", lambda: 1_800_000_001)
    result = api.dealer_command_center(api.GroupScopedRequest(selected_groups=["live"]))
    assert result["status"] == "connected"
    assert result["kpis"]["total_equity"] == 1100
    assert result["kpis"]["total_credit"] == 200
    assert result["top_risk_accounts"][0]["credit"] == 200
    assert result["kpis"]["open_positions"] == 1
    assert result["symbol_exposure"][0]["gross_notional"] is None
    assert result["symbol_exposure"][0]["risk_status"] == "data_required"
    assert result["what_changed"] == []


def test_command_center_never_adds_unknown_currencies(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "command-center-mixed.db")
    api.init_db()
    users = [{"Login": 1001, "Group": "live", "Currency": "USD", "Equity": 100}, {"Login": 1002, "Group": "live", "Currency": "EUR", "Equity": 100}]
    monkeypatch.setattr(api, "get_configured_manager", lambda: FakeManager(users, []))
    monkeypatch.setattr(api.time_module, "time", lambda: 1_800_000_001)
    result = api.dealer_command_center(api.GroupScopedRequest(selected_groups=["live"]))
    assert result["currency"] is None
    assert result["kpis"]["total_equity"] is None
    assert result["kpis"]["total_credit"] is None
    assert result["kpis"]["total_accounts"] == 2


def test_command_center_reports_offline_without_fake_values(monkeypatch):
    def offline():
        raise RuntimeError("MT5 unavailable")
    monkeypatch.setattr(api, "get_configured_manager", offline)
    result = api.dealer_command_center(api.GroupScopedRequest(selected_groups=["live"]))
    assert result["status"] == "offline"
    assert result["kpis"] is None
