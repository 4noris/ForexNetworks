from mt5_connection import MT5ConnectionManager


def test_live_version_advances_without_changing_verified_observation_time():
    manager = MT5ConnectionManager("fixture", 1, "")
    manager.native_mode = True
    manager.cache.update(native_epoch="first", native_sequence=100, last_update=123)
    assert manager.live_view_marker() == ("first", 100)
    manager.cache["native_sequence"] = 101
    assert manager.live_view_marker() == ("first", 101)
    assert manager.update_marker() == 123
    manager.cache["native_epoch"] = "new-source"
    assert manager.live_view_marker() == ("new-source", 101)


def test_legacy_marker_retains_verified_source_time():
    manager = MT5ConnectionManager("fixture", 1, "")
    manager.cache["last_update"] = 123
    assert manager.live_view_marker() == 123


def test_websocket_rebuilds_on_callback_version_between_full_observations(monkeypatch):
    import os
    import time
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import mt5_connection
    from services.live_stream import install
    manager = MT5ConnectionManager("fixture", 1, "")
    manager.native_mode = True
    manager.api = object()
    stamp = time.time()
    manager.cache.update(last_update=stamp, native_epoch="fixture", native_sequence=1, errors=[])
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: manager)
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    application = FastAPI()
    install(SimpleNamespace(api=application, os=os, SESSION_COOKIE_NAME="session",
        _verify_session_token=lambda token: {"id": 1}, LiveRiskRequest=lambda **params: params,
        get_live_risk=lambda params: {"value": manager.cache["native_sequence"], "source_updated_at": stamp}))
    with TestClient(application) as client:
        client.cookies.set("session", "fixture")
        with client.websocket_connect("/api/live/ws") as socket:
            socket.send_json({"type": "subscribe", "id": "book", "topic": "live-risk", "params": {"selected_groups": ["fixture"]}})
            first = socket.receive_json()
            assert first["data"]["value"] == 1
            manager.cache["native_sequence"] = 2
            started = time.monotonic()
            second = socket.receive_json()
            assert time.monotonic() - started < 1.6  # Old timestamp cache waited two seconds.
            if second["type"] == "snapshot":
                assert second["data"]["value"] == 2
                assert second["data"]["source_updated_at"] == stamp
            else:
                assert {"path": ["value"], "value": 2} in second["changes"]
                assert not any(change["path"] == ["source_updated_at"] for change in second["changes"])
    assert manager.update_marker() == stamp
