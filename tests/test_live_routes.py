import asyncio
import threading

import httpx2 as httpx
from fastapi import FastAPI, Response
from pydantic import BaseModel

from services.live_routes import LiveReadRoute


def test_slow_read_encoding_does_not_block_other_live_requests(monkeypatch):
    import services.live_routes as routes
    entered, release = threading.Event(), threading.Event()
    original=routes.jsonable_encoder

    def delayed(data,**kwargs):
        if data=={"slow":True}:
            entered.set()
            assert release.wait(3)
        return original(data,**kwargs)

    monkeypatch.setattr(routes,"jsonable_encoder",delayed)
    app=FastAPI()
    app.router.route_class=LiveReadRoute
    @app.get("/api/manager/accounts")
    def accounts():return {"slow":True}
    @app.get("/api/health")
    async def health():return {"ok":True}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            slow=asyncio.create_task(client.get("/api/manager/accounts"))
            try:
                assert await asyncio.to_thread(entered.wait,2)
                fast=await asyncio.wait_for(client.get("/api/health"),.5)
                assert fast.json()=={"ok":True}
            finally:
                release.set()
                await slow
    asyncio.run(scenario())


def test_response_models_still_filter_private_fields_and_headers_survive():
    app=FastAPI()
    app.router.route_class=LiveReadRoute
    class Public(BaseModel):login:int
    @app.get("/api/manager/accounts",response_model=Public)
    def accounts():return {"login":7,"secret":"must be filtered"}
    @app.post("/api/operations/live")
    def operations(response:Response):
        response.headers["X-Source"]="native"
        response.status_code=202
        return {"equity":123.45}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            assert (await client.get("/api/manager/accounts")).json()=={"login":7}
            response=await client.post("/api/operations/live")
            assert response.status_code==202
            assert response.headers["X-Source"]=="native"
            assert response.json()=={"equity":123.45}
    asyncio.run(scenario())
