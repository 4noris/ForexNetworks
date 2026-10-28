from unittest.mock import patch

from fastapi.testclient import TestClient

import api
from services.spread_alerts import SpreadAlertService


def test_spread_controls_are_authenticated_admin_only_and_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "controls.db"))
    api.init_db()
    service = SpreadAlertService(api._conn, lambda *a, **k: (True, "test-only"), lambda: False)
    monkeypatch.setattr(api, "SPREAD_ALERTS", service)
    client = TestClient(api.api)  # No lifespan: never connect to MT5 or deliver alerts.
    assert client.get("/api/notifications/spread/settings").status_code == 401
    with patch.object(api, "_verify_session_token", return_value={"id": 1, "username": "viewer", "is_admin": False}):
        client.cookies.set(api.SESSION_COOKIE_NAME, "test-only")
        assert client.get("/api/notifications/spread/settings").status_code == 403
        assert client.put("/api/notifications/spread/settings", json={}).status_code == 403
    with patch.object(api, "_verify_session_token", return_value={"id": 1, "username": "admin", "is_admin": True}):
        assert client.get("/api/notifications/spread/settings").json()["settings"]["enabled"] is False
        assert client.put("/api/notifications/spread/settings", json={"enabled": True}).status_code == 400
        result = client.put("/api/notifications/spread/settings", json={"enabled": True,
            "default_threshold_points": 30, "symbol_thresholds": {"XAUUSD.x": 45}, "cooldown_seconds": 60})
        assert result.status_code == 200
        assert result.json()["settings"]["symbol_thresholds"] == {"XAUUSD.x": 45}
        restored = SpreadAlertService(api._conn, lambda *a, **k: None, lambda: False)
        assert restored.read_settings().default_threshold_points == 30
        assert client.put("/api/notifications/spread/settings", json={"default_threshold_points": -1}).status_code == 422
        with api._conn() as con:
            assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='settings.spread_alerts_updated'").fetchone()[0] == 1


def test_symbol_search_uses_existing_cache_and_rejects_unavailable_feed(monkeypatch):
    import threading
    from types import SimpleNamespace
    manager = SimpleNamespace(is_connected=True, cache_lock=threading.Lock(),
        cache={"symbol_cache": {"XAUUSD.x": {"Description": "Gold", "Digits": 2}}})
    monkeypatch.setattr(api, "_risk_manager_provider", lambda: manager)
    assert api.spread_symbol_catalog("gold", 20)["symbols"] == [
        {"symbol": "XAUUSD.x", "description": "Gold", "digits": 2}]
    manager.is_connected = False
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as failure:
        api.spread_symbol_catalog("gold", 20)
    assert failure.value.status_code == 503
