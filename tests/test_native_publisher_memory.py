import threading

import pytest

from services.native_bridge import NativeBridge,NativeProjection
from test_native_bridge import Manager,snapshot


@pytest.mark.parametrize("stage",["capture","metrics"])
def test_publisher_survives_allocation_failure_and_publishes_after_resnapshot(monkeypatch,stage):
    monkeypatch.setenv("RMS_NATIVE_URL","http://test-only")
    monkeypatch.setenv("RMS_NATIVE_TOKEN","test-only")
    manager=Manager()
    manager.stop_event=threading.Event()
    published=threading.Event()
    class Cache(dict):
        def update(self,value):
            super().update(value)
            if value.get("status")=="connected":published.set()
    manager.cache=Cache()
    bridge=NativeBridge(manager)
    bridge.state.snapshot(snapshot())
    bridge.available=True
    bridge.core_pending=True
    failed=threading.Event()
    unavailable=bridge.unavailable
    def observed_failure(reason):
        unavailable(reason)
        failed.set()
    bridge.unavailable=observed_failure
    if stage=="capture":
        def allocate_failure(*args):raise MemoryError("test capture pressure")
        bridge.state.capture=allocate_failure
    else:
        original=manager._build_metrics
        calls=[]
        def allocate_failure(*args):
            calls.append(True)
            if len(calls)==1:raise MemoryError("test metrics pressure")
            return original(*args)
        manager._build_metrics=allocate_failure
    worker=threading.Thread(target=bridge._publish_loop)
    worker.start()
    bridge.publish_event.set()
    try:
        assert failed.wait(2)
        assert worker.is_alive()
        assert manager.cache["status"]=="disconnected"
        with bridge.state_lock:
            bridge.state=NativeProjection()
            bridge.state.snapshot(snapshot())
            bridge.available=True
            bridge.core_pending=True
            bridge.revision+=1
        bridge.publish_event.set()
        assert published.wait(3)
        assert manager.cache["users_by_login"][7]["Equity"]==127
    finally:
        manager.stop_event.set()
        bridge.close()
        worker.join(2)
    assert not worker.is_alive()
