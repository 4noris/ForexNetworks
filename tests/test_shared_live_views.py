import asyncio
import threading

import pytest

from services.live_stream import SharedLiveViews, view_key


def test_many_subscribers_share_build_and_survive_one_tab_cancellation():
    started, release = threading.Event(), threading.Event()
    calls = []

    def build(value):
        calls.append(value)
        started.set()
        assert release.wait(3)
        return {"value": value}

    async def scenario():
        views = SharedLiveViews(build)
        tasks = [asyncio.create_task(views.read("same-authorized-scope", 7)) for _ in range(40)]
        try:
            assert await asyncio.to_thread(started.wait, 2)
            await asyncio.sleep(0)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            release.set()
            results = await asyncio.gather(*tasks[1:])
            assert all(row == {"value": 7} for row in results)
            assert calls == [7]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_different_users_and_filters_keep_separate_view_keys():
    first = view_key("accounts", {"q": "one"}, {"id": 1, "username": "first"})
    assert first != view_key("accounts", {"q": "one"}, {"id": 2, "username": "second"})
    assert first != view_key("accounts", {"q": "two"}, {"id": 1, "username": "first"})


def test_pending_view_work_is_bounded_and_build_errors_reach_all_waiters():
    started, release = threading.Event(), threading.Event()

    def fail():
        started.set()
        assert release.wait(3)
        raise ValueError("source unavailable")

    async def scenario():
        views = SharedLiveViews(fail, workers=1, pending=1)
        one = asyncio.create_task(views.read("first"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            two = asyncio.create_task(views.read("first"))
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError, match="busy"):
                await views.read("different-scope")
            release.set()
            for result in await asyncio.gather(one, two, return_exceptions=True):
                assert isinstance(result, ValueError)
            await asyncio.sleep(0)
            assert not views.tasks
        finally:
            release.set()
            await asyncio.gather(one, return_exceptions=True)

    asyncio.run(scenario())
