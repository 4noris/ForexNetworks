import os
import statistics
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import mt5_connection
from mt5_connection import MT5ConnectionManager
from services.live_stream import install


def test_native_callbacks_reach_subscriber_without_rewriting_source_time(monkeypatch):
    manager = MT5ConnectionManager("fixture", 1, "")
    manager.native_mode = True
    manager.api = object()
    stamp = time.time()
    manager.cache.update(last_update=stamp, native_epoch="fixture", native_sequence=1, errors=[])
    monkeypatch.setenv("RMS_NATIVE_URL", "http://fixture-unused")
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: manager)
    monkeypatch.setattr(mt5_connection, "FULL_SNAPSHOT_ENABLED", True)
    application = FastAPI()
    install(SimpleNamespace(api=application, os=os, SESSION_COOKIE_NAME="session",
        _verify_session_token=lambda token: {"id": 1}, LiveRiskRequest=lambda **params: params,
        get_live_risk=lambda params: {"value": manager.cache["native_sequence"], "source_updated_at": stamp}))
    delays = []
    with TestClient(application) as client:
        client.cookies.set("session", "fixture")
        with client.websocket_connect("/api/live/ws") as socket:
            socket.send_json({"type":"subscribe", "id":"book", "topic":"live-risk", "params":{"selected_groups":["fixture"]}})
            first = socket.receive_json()
            assert first["type"] == "snapshot", first
            assert first["data"]["value"] == 1
            for sequence in (2, 3, 4):
                manager.cache["native_sequence"] = sequence
                started = time.monotonic()
                frame = socket.receive_json()
                delays.append(time.monotonic() - started)
                assert frame["type"] in {"snapshot", "delta"}
                if frame["type"] == "snapshot":
                    assert frame["data"] == {"value": sequence, "source_updated_at": stamp}
                else:
                    assert {"path":["value"], "value":sequence} in frame["changes"]
                    assert not any(change["path"] == ["source_updated_at"] for change in frame["changes"])
    assert statistics.median(delays) < .24
    assert max(delays) < .6
    assert manager.update_marker() == stamp
