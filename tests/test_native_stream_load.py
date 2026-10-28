import copy
import io
import json
import threading
import time

from services.native_bridge import NativeBridge, NativeProjection
from test_native_bridge import Manager, snapshot


class JSONResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


def test_slow_publication_does_not_block_or_reconnect_native_event_reader(monkeypatch):
    monkeypatch.setenv("RMS_NATIVE_URL", "http://test-only")
    monkeypatch.setenv("RMS_NATIVE_TOKEN", "test-only")
    manager = Manager()
    manager.stop_event = threading.Event()
    publishing, release, drained, finished, latest = (threading.Event() for _ in range(5))
    calls = []
    builds = 0
    original = manager._build_metrics

    def slow_build(users, positions):
        nonlocal builds
        builds += 1
        if builds == 1:
            publishing.set()
            assert release.wait(5)
        return original(users, positions)

    class Cache(dict):
        def update(self, value):
            super().update(value)
            if value.get("users_by_login", {}).get(7, {}).get("Equity") == 49999:
                latest.set()

    manager.cache = Cache()
    manager._build_metrics = slow_build
    source = snapshot()
    account = source["records"]["accounts"]["7"]

    class Stream:
        index = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            finished.set()

        def readline(self, limit):
            assert publishing.wait(3)
            if self.index > 50000:
                drained.set()
                finished.wait(5)
                return b""
            i = self.index
            self.index += 1
            event = {"kind": "accounts", "op": "upsert" if i < 50000 else "cycle_end",
                     "id": "7" if i < 50000 else "", "epoch": source["epoch"],
                     "sequence": 11 + i, "received_ms": int(time.time() * 1000),
                     "data": dict(account, Equity=i) if i < 50000 else {}}
            return (json.dumps(event) + "\n").encode()

    stream = Stream()
    bridge = NativeBridge(manager)

    def request(path, timeout=5):
        calls.append(path.split("?")[0])
        if path.endswith("health"):
            return JSONResponse({"ready": True})
        if path.endswith("snapshot"):
            return JSONResponse(source)
        return stream

    bridge.request = request
    reader = threading.Thread(target=bridge.run)
    reader.start()
    try:
        assert publishing.wait(3)
        # More than the gateway's 32,768-event backlog, while publishing is blocked.
        assert drained.wait(4)
        assert calls.count("/internal/native/changes") == 1
        assert bridge.state.records["accounts"]["7"]["Equity"] == 49999
        release.set()
        assert latest.wait(3)
    finally:
        release.set()
        manager.stop_event.set()
        bridge.close()
        reader.join(3)
        bridge.publisher.join(3)
    assert not reader.is_alive()
    assert not bridge.publisher.is_alive()


def test_captured_rows_cannot_change_during_a_slow_publication():
    state = NativeProjection()
    state.snapshot(snapshot())
    view = state.capture()
    changed = copy.copy(state.records["accounts"]["7"])
    changed["Equity"] = 300
    state.apply({"kind": "accounts", "op": "upsert", "id": "7", "data": changed,
                 "sequence": 11, "epoch": state.epoch})
    assert view.records["accounts"]["7"]["Equity"] == 127
    manager = Manager()
    view.publish(manager)
    next_view = state.capture(view)
    next_view.publish(manager)
    assert manager.cache["users_by_login"][7]["Equity"] == 300


def test_disconnected_generation_cannot_be_published_after_source_reset():
    state = NativeProjection()
    state.snapshot(snapshot())
    manager = Manager()
    manager.cache["status"] = "disconnected"
    assert state.publish(manager, lambda: False) is False
    assert manager.cache["status"] == "disconnected"
    assert "users" not in manager.cache
