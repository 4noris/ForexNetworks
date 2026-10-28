from unittest.mock import patch
import threading
import time

import pytest
from fastapi import HTTPException

import api


@pytest.mark.parametrize("age,errors,status", [
    (20, [], "connected"), (0, ["resync"], "connected"),
    (0, [], "disconnected"), (float("nan"), [], "connected"),
    (-60, [], "connected"),
])
def test_native_readiness_rejects_stale_or_incomplete_generation(monkeypatch, age, errors, status):
    import mt5_connection

    class Manager:
        is_connected = True
        native_mode = True
        cache_lock = threading.Lock()
        cache = {"status": status, "last_update": time.time() - age, "errors": errors}

    monkeypatch.setattr(mt5_connection, "get_manager", lambda: Manager())
    with pytest.raises(HTTPException) as error:
        api.readiness()
    assert error.value.status_code == 503


def test_native_readiness_accepts_current_broker_data(monkeypatch):
    import mt5_connection

    class Manager:
        is_connected = True
        native_mode = True
        cache_lock = threading.Lock()
        cache = {"status": "connected", "last_update": time.time() - 1, "errors": []}

        def market_cache_status(self):
            return {}

    monkeypatch.setattr(mt5_connection, "get_manager", lambda: Manager())
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    assert api.readiness()["status"] == "ready"


def test_manager_status_reads_generation_marker_without_copying_accounts(monkeypatch):
    class Manager:
        is_connected = True
        server = "example:443"
        manager_login = 1

        def update_marker(self):
            return 100.0

        def snapshot(self):
            raise AssertionError("Status must not copy the account snapshot")

    import mt5_connection

    monkeypatch.setattr(mt5_connection, "get_manager", lambda: Manager())
    result = api.manager_status()

    assert result["status"] == "connected"
    assert result["last_update"] == 100.0


def test_manager_dashboard_does_not_claim_live_before_first_generation():
    class Manager:
        is_connected = True
        server = "example:443"
        manager_login = 1

        def dashboard_cache_view(self):
            return {
                "users": [], "positions_count": 0, "summary_rows": [],
                "exposure_rows": [], "metrics": {}, "last_update": 0.0,
                "status": "disconnected", "errors": [],
                "refresh_phase": "staging",
            }

    with patch.object(api, "get_configured_manager", return_value=Manager()):
        result = api.manager_dashboard(None)

    assert result["status"] == "warming"
    assert result["data_age_seconds"] is None
