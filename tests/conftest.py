"""Unit/API tests must not launch an archive against the deployment database."""
import os
from pathlib import Path
import tempfile
import pytest

# Collection imports api.py before fixtures run; isolate its startup schema writes too.
os.environ.setdefault("DB_PATH", str(Path(tempfile.gettempdir()) / f"rms-test-bootstrap-{os.getpid()}.db"))
# The application intentionally reloads .env with override=True; keep this test-only
# override separate so a relative production DB_PATH cannot replace it.
os.environ.setdefault("RMS_TEST_DB_PATH", str(Path(tempfile.gettempdir()) / f"rms-test-bootstrap-{os.getpid()}.db"))


@pytest.fixture(autouse=True)
def disable_implicit_historical_worker(monkeypatch):
    # Worker tests instantiate their own HistoryWorker and temporary database.
    # Individual integration tests can explicitly override this inside the test.
    monkeypatch.setenv("HISTORY_WORKER_ENABLED", "false")
