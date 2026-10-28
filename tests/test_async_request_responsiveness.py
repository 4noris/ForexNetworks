"""Slow session storage must not freeze unrelated HTTP work."""
import asyncio
import threading

from fastapi import Request, Response
import api


def test_slow_session_check_keeps_event_loop_available(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def verify(_token):
        started.set()
        release.wait(2)
        return {"id": 1, "username": "test", "is_admin": 1, "must_change_pw": 0}

    monkeypatch.setattr(api, "_verify_session_token", verify)

    async def run():
        request = Request({"type": "http", "method": "GET", "path": "/api/manager/status", "headers": [(b"authorization", b"Bearer test")]})

        async def next_handler(_request):
            return Response(status_code=200)

        task = asyncio.create_task(api.authenticate_api_requests(request, next_handler))
        try:
            await asyncio.sleep(0.05)
            assert started.is_set()
            assert not task.done(), "The event loop must run while session storage is waiting"
        finally:
            release.set()
        response = await task
        assert response.status_code == 200
        assert request.state.user["username"] == "test"

    asyncio.run(run())
