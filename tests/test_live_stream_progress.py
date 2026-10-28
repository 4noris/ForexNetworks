import os
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.live_stream import install


def test_unchanged_view_reports_progress_without_changing_source_time():
    application = FastAPI()
    stamp = time.time() - 60
    calls = []

    def unchanged(_request):
        calls.append(1)
        return {"value": 7, "source_updated_at": stamp}

    install(SimpleNamespace(api=application, os=os, SESSION_COOKIE_NAME="session",
        _verify_session_token=lambda _token: {"id": 1}, manager_dashboard=unchanged))
    with TestClient(application) as client:
        client.cookies.set("session", "fixture")
        with client.websocket_connect("/api/live/ws") as socket:
            socket.send_json({"type": "subscribe", "id": "view", "topic": "dashboard"})
            snapshot = socket.receive_json()
            assert snapshot["data"]["source_updated_at"] == stamp
            progress = socket.receive_json()
            assert progress == {"id": "view", "type": "progress", "version": snapshot["version"]}
            assert len(calls) >= 2
            assert "source_updated_at" not in progress
