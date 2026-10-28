from __future__ import annotations

import mt5_connection
from mt5_connection import MT5ConnectionManager


class FakeApi:
    def __init__(self) -> None:
        self.disconnected = False

    def Disconnect(self) -> None:
        self.disconnected = True


def test_failed_reconnect_drops_stale_sdk_handle(monkeypatch):
    manager = MT5ConnectionManager("server", 1, "secret")
    stale = FakeApi()
    manager.api = stale
    monkeypatch.setattr(mt5_connection, "RECONNECT_BACKOFF_SECONDS", 0)

    def fail_connect():
        raise RuntimeError("temporary SDK failure")

    monkeypatch.setattr(manager, "_create_connected_api", fail_connect)
    manager._attempt_reconnect("watchdog failed")

    assert stale.disconnected is True
    assert manager.api is None
    assert manager.is_connected is False
    assert manager.snapshot().status == "disconnected"
    assert manager.snapshot().refresh_phase == "reconnect_failed"


def test_successful_reconnect_atomically_publishes_new_handle(monkeypatch):
    manager = MT5ConnectionManager("server", 1, "secret")
    stale = FakeApi()
    replacement = FakeApi()
    manager.api = stale
    monkeypatch.setattr(mt5_connection, "RECONNECT_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(manager, "_create_connected_api", lambda: replacement)

    manager._attempt_reconnect("watchdog failed")

    assert stale.disconnected is True
    assert manager.api is replacement
    assert manager.is_connected is True
    assert manager.snapshot().status == "reconnecting"
    assert manager.last_refresh["users"] == 0.0


def test_shutdown_interrupts_reconnect_backoff(monkeypatch):
    manager = MT5ConnectionManager("server", 1, "secret")
    stale = FakeApi()
    manager.api = stale
    manager.stop_event.set()
    monkeypatch.setattr(mt5_connection, "RECONNECT_BACKOFF_SECONDS", 60)

    manager._attempt_reconnect("shutdown")

    assert manager.api is stale
    assert stale.disconnected is False
