import threading
import time
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

import api
import mt5_connection


def manager_fixture():
    return SimpleNamespace(is_connected=True, cache_lock=threading.RLock(), cache={
        "status": "connected", "last_update": time.time(), "errors": [], "groups": ["real"],
        "users_by_login": {7: {"Login": 7, "Name": "Target", "Group": "real", "Balance": 100,
            "Equity": 90, "Credit": 0, "Margin": 20, "Free Margin": 70, "Margin Level %": 450}},
        "positions_by_login": {7: [{"Login": 7, "Ticket": 1, "Symbol": "EURUSD", "Action": 0,
            "Volume": 0.05, "ContractSize": 100000, "PriceCurrent": 1.1, "PriceOpen": 1.2, "Profit": -10, "Swap": 0}]},
    })


def test_client_cache_uses_exact_account_without_native_sdk(monkeypatch):
    manager = manager_fixture()
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: manager)
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    def forbidden(*args, **kwargs):
        raise AssertionError("Native session should not be opened for a fresh cached account")
    monkeypatch.setattr(api, "market_manager_session", forbidden)
    result = api.get_client_live(api.ClientLiveRequest(client_login=7, selected_groups=["real"]))
    assert result["client"]["login"] == 7
    assert result["positions"][0]["lots"] == 0.05
    assert result["summary"]["equity"] == 90
    assert result["cache"]["generation"] == manager.cache["last_update"]
    with pytest.raises(HTTPException):
        api.get_client_live(api.ClientLiveRequest(client_login=7, selected_groups=["other"]))
    with pytest.raises(HTTPException):
        api.get_client_live(api.ClientLiveRequest(client_login=7, selected_groups=["real"], excluded_logins=[7]))


def test_stale_or_incomplete_cache_never_pretends_to_be_live(monkeypatch):
    manager = manager_fixture()
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: manager)
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    manager.cache["last_update"] = time.time() - 60
    assert api._cached_client_read(7) is None
    manager.cache["last_update"] = time.time()
    manager.cache["errors"] = ["incomplete"]
    assert api._cached_client_read(7) is None
