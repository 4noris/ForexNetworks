import threading
import time
from types import SimpleNamespace

import pytest

import api
import mt5_connection


@pytest.mark.parametrize("source_connected", [True, False])
def test_delayed_observations_do_not_claim_a_transport_reconnect(monkeypatch, source_connected):
    stamp = time.time() - 20
    manager = SimpleNamespace(
        is_connected=True, native_mode=True, server="test-only", manager_login=1,
        cache_lock=threading.Lock(), cache={"status": "connected", "errors": []},
        update_marker=lambda: stamp, native_bridge=SimpleNamespace(available=source_connected),
    )
    monkeypatch.setattr(mt5_connection, "get_manager", lambda: manager)
    status = api.manager_status()
    assert status["status"] == "warming"  # Never call old account observations live.
    assert status["source_connected"] is source_connected
    assert status["last_update"] == stamp
    assert "reconnecting" not in status["error"].lower()
