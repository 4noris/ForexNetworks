import io
import threading

from services.native_bridge import NativeBridge
from test_native_bridge import Manager


def test_snapshot_allocation_failure_does_not_kill_reader(monkeypatch):
    monkeypatch.setenv("RMS_NATIVE_URL", "http://test-only")
    monkeypatch.setenv("RMS_NATIVE_TOKEN", "test-only")
    manager = Manager()
    manager.stop_event = threading.Event()
    bridge = NativeBridge(manager)
    health_calls = []

    def request(path, timeout=5):
        if path.endswith("health"):
            health_calls.append(path)
            if len(health_calls) == 2:
                manager.stop_event.set()
                return io.BytesIO(b'{"ready":false}')
            return io.BytesIO(b'{"ready":true}')
        raise MemoryError("test allocation failure")

    bridge.request = request
    bridge.run()
    bridge.publisher.join(2)
    assert len(health_calls) == 2
    assert not bridge.publisher.is_alive()
    assert manager.cache["status"] == "disconnected"
    assert manager.cache["users"] == []
