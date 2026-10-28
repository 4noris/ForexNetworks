from types import SimpleNamespace

import api


def test_risk_event_deduplication_survives_new_scan_and_preserves_dealer_status(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "risk-events.db")
    api.init_db()
    monkeypatch.setattr(api, "_operations_admin_actor", lambda _request: "dealer")
    event = {"rule": "latency_arbitrage", "login": 1001, "position_id": 77, "symbol": "EURUSD", "group": "live", "severity": "warning", "timestamp": "2026-10-01T00:00:00+00:00", "evidence": "Exact tick timing", "thresholds": {"minimum_gap_seconds": 3}, "triggering_values": {"gap_seconds": 5}, "data_quality": "VERIFIED"}
    assert len(api.persist_antifraud_events([event], source_generation=10)) == 1
    listed = api.list_risk_events(None, login=1001, status=None, limit=100, offset=0)
    assert listed["total"] == 1
    event_id = listed["events"][0]["event_id"]
    assert listed["events"][0]["status"] == "NEW"
    updated = api.update_risk_event_status(event_id, api.RiskEventActionRequest(status="ACKNOWLEDGED", note="Dealer reviewed source ticks"), None)
    assert updated["status"] == "ACKNOWLEDGED"
    assert updated["acknowledged_by"] == "dealer"
    assert api.persist_antifraud_events([{**event, "evidence": "Exact tick timing updated", "triggering_values": {"gap_seconds": 6}}], source_generation=11) == []
    detail = api.risk_event_detail(event_id, None)
    assert detail["event"]["status"] == "ACKNOWLEDGED"
    assert detail["event"]["actual_values"]["gap_seconds"] == 6
    assert detail["actions"][0]["note"] == "Dealer reviewed source ticks"


def test_unchecked_candidate_is_never_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "unchecked.db")
    api.init_db()
    assert api.persist_antifraud_events([{"rule": "latency_arbitrage", "login": 1001, "data_quality": "UNCHECKED"}]) == []
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0] == 0


def test_deposit_alert_is_one_persistent_event_per_deal(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "deposits.db")
    api.init_db()
    base = {"rule": "deposit_alert", "login": 1001, "deal_id": 501,
            "group": "live", "severity": "warning", "timestamp": "2026-10-01T00:00:00+00:00",
            "evidence": "MT5 deposit", "thresholds": {"minimum_deposit_amount": 1000},
            "triggering_values": {"deposit_amount": 1200}, "data_quality": "PARTIAL"}
    assert len(api.persist_antifraud_events([base])) == 1
    assert api.persist_antifraud_events([{**base, "thresholds": {"minimum_deposit_amount": 500}}]) == []
    assert len(api.persist_antifraud_events([{**base, "deal_id": 502}])) == 1
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0] == 2


def test_in_app_notifications_read_new_persisted_alerts(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "in-app.db")
    api.init_db()
    version = api._IN_APP_RISK_EVENT_VERSION
    event = {"rule": "deposit_alert", "login": 1001, "deal_id": 501,
             "group": "live", "severity": "warning", "timestamp": "2026-10-01T00:00:00+00:00",
             "evidence": "Deposit 1200 USD", "data_quality": "PARTIAL"}
    api.persist_antifraud_events([event])
    assert api._IN_APP_RISK_EVENT_VERSION == version + 1
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "admin", "is_admin": True}))
    result = api.in_app_notifications(request, 50)
    assert len(result["events"]) == 1
    assert result["events"][0]["evidence"] == "Deposit 1200 USD"
    assert result["events"][0]["detector"] == "deposit_alert"


def test_background_scope_uses_persisted_groups_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "monitor.db")
    api.init_db()
    request = api.AntiFraudRequest(selected_groups=["live"], server="ignored", login=7, password="must-not-store")
    api._remember_antifraud_monitor_scope(request, "scope-1")
    with api._conn() as con:
        row = con.execute("SELECT request_json FROM antifraud_monitor_scopes WHERE cache_key='scope-1'").fetchone()
        assert "must-not-store" not in row["request_json"]
        con.execute("UPDATE antifraud_monitor_scopes SET last_scan_at='2000-01-01 00:00:00' WHERE cache_key='scope-1'")
    captured = []
    monkeypatch.setattr(api, "get_antifraud_snapshot", lambda req: captured.append(req) or {"status": "ready", "warnings": []})
    assert api._scan_next_monitored_antifraud_scope() is True
    assert captured[0].selected_groups == ["live"]
    assert captured[0].refresh_token > 0
    assert captured[0].password == ""
