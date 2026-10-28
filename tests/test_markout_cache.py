from concurrent.futures import ThreadPoolExecutor
import time

from services.markout_cache import MarkoutReportCache


def test_rpc_and_subscription_share_one_history_scan():
    cache = MarkoutReportCache()
    builds = []
    def build():
        builds.append(1)
        time.sleep(.02)
        return {"verified": True}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cache.read(("source", "scope"), build), range(2)))
    assert len(builds) == 1
    assert results == [{"verified": True}, {"verified": True}]


def test_cache_is_bounded_and_never_reuses_another_manager_or_scope(monkeypatch):
    cache = MarkoutReportCache(limit=2)
    clock = [100]
    monkeypatch.setattr("services.markout_cache.time.monotonic", lambda: clock[0])
    assert cache.read(("one", "A"), lambda: 1) == 1
    assert cache.read(("two", "A"), lambda: 2) == 2
    assert cache.read(("two", "B"), lambda: 3) == 3
    assert len(cache.entries) == 2
    assert ("one", "A") not in cache.entries
    clock[0] = 130
    assert cache.read(("two", "A"), lambda: 4) == 4
    assert len(cache.entries) == 1
