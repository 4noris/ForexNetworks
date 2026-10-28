from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from mt5_connection import MT5ConnectionManager


@pytest.mark.parametrize("reader", ["snapshot", "dashboard_cache_view", "live_risk_cache_view"])
@pytest.mark.parametrize("native", [True, False])
def test_large_readers_release_native_publication_lock(reader, native):
    entered, release = threading.Event(), threading.Event()

    class SlowRow(Mapping):
        data = {"Login": 7, "Group": "real", "Equity": 20}

        def __iter__(self):
            entered.set()
            assert release.wait(5)
            return iter(self.data)

        def __len__(self):
            return len(self.data)

        def __getitem__(self, key):
            return self.data[key]

        def __deepcopy__(self, memo):
            entered.set()
            assert release.wait(5)
            return dict(self.data)

    manager = MT5ConnectionManager("test-only", 1, "")
    manager.native_mode = native
    manager.cache["users"] = [SlowRow()]
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(getattr(manager, reader))
        try:
            assert entered.wait(3)
            acquired = manager.cache_lock.acquire(blocking=False)
            if acquired:
                manager.cache["last_update"] = 999
                manager.cache_lock.release()
            assert acquired is native
        finally:
            release.set()
        view = result.result(timeout=5)
    # An in-flight reader retains a consistent generation after publication.
    assert (view.last_update if reader == "snapshot" else view["last_update"]) == 0


def test_native_report_mutations_cannot_change_live_cache():
    manager = MT5ConnectionManager("test-only", 1, "")
    manager.native_mode = True
    manager.cache.update(
        users=[{"Login": 7, "Equity": 20}],
        positions=[{"Ticket": "1", "Profit": 3}],
        symbol_cache={"EURUSD": {"Digits": 5}},
        tick_cache={"EURUSD": {"bid": 1.1}},
        metrics={"top_margin_accounts": [{"login": 7}]},
    )
    view = manager.snapshot()
    view.users[0]["Equity"] = 999
    view.positions[0]["Profit"] = 999
    view.symbol_cache["EURUSD"]["Digits"] = 0
    view.tick_cache["EURUSD"]["bid"] = 0
    view.metrics["top_margin_accounts"][0]["login"] = 99
    assert manager.cache["users"][0]["Equity"] == 20
    assert manager.cache["positions"][0]["Profit"] == 3
    assert manager.cache["symbol_cache"]["EURUSD"]["Digits"] == 5
    assert manager.cache["tick_cache"]["EURUSD"]["bid"] == 1.1
    assert manager.cache["metrics"]["top_margin_accounts"][0]["login"] == 7
