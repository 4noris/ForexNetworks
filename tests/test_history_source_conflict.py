import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.historical import ArchiveSourceMismatch, HistoryStore
from services.history_worker import HistoryWorker


def test_read_guard_is_read_only_and_rejects_another_source(tmp_path):
    store = HistoryStore(tmp_path / "archive.db")
    store.migrate("2026-10-01")
    store.verify_source("configured")
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM history_source").fetchone()[0] == 0
    store.bind_source("original")
    with pytest.raises(ArchiveSourceMismatch):
        store.verify_source("configured")
    store.verify_source("original")


def test_worker_blocks_source_conflict_once_without_claiming_exports_and_recovers(tmp_path, monkeypatch, caplog):
    store = HistoryStore(tmp_path / "archive.db")
    store.migrate("2026-10-01")
    store.bind_source("original")
    identity = ["other"]
    clock = [100.0]
    monkeypatch.setattr("services.history_worker.time.monotonic", lambda: clock[0])
    provider = SimpleNamespace(check_source=lambda: store.bind_source(identity[0]))
    worker = HistoryWorker(store, provider, threading.Event())
    monkeypatch.setattr(store, "claim_export", lambda owner: pytest.fail("Conflicted export must not be claimed"))
    assert not worker.source_available()
    worker.process_export()
    clock[0] += 6
    assert not worker.source_available()
    assert sum("Historical processing blocked" in record.message for record in caplog.records) == 1
    identity[0] = "original"
    clock[0] += 6
    assert worker.source_available()
    assert worker.source_error is None


def test_conflicted_reports_and_sync_return_409_while_status_remains_readable(tmp_path, monkeypatch):
    from services.history_api import install
    from fastapi import FastAPI

    application = FastAPI()
    instance = SimpleNamespace(api=application, DB_PATH=tmp_path / "app.db",
        _RUNTIME_MAINTENANCE_STOP=threading.Event(),
        _operations_admin_actor=lambda request: "fixture-admin",
        configured_manager_credentials=lambda: ("fixture-source", 1, ""),
        get_book_group_settings=lambda: {}, write_audit=lambda *args: None)
    worker = install(instance)
    worker.store.bind_source("another-source")
    monkeypatch.setattr("mt5_connection.get_manager", lambda: None)
    with TestClient(application) as client:
        response = client.get("/api/dealing-risk/sync/status")
        assert response.status_code == 200
        assert response.json()["sync_blocked"] is True
        query = "?from_date=2026-10-01&to_date=2026-10-25"
        for report in ("pnl", "overview", "exposure", "accounts", "trading-activity", "risk-events", "catalog"):
            assert client.get("/api/dealing-risk/analytics/" + report + query).status_code == 409
        assert client.post("/api/dealing-risk/sync/run", json={}).status_code == 409
        assert client.post("/api/dealing-risk/analytics/exports", json={"from_date":"2026-10-01","to_date":"2026-10-25"}).status_code == 409
    worker.store.verify_source("another-source")
