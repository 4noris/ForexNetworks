import copy
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import asyncio
import re
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

try:
    from services.live_stream import changes, frame, install, subscription
except ModuleNotFoundError:
    import sys
    from types import ModuleType
    package = ModuleType("rms_stream_stage")
    package.__path__ = [str(Path(__file__).parent)]
    sys.modules["rms_stream_stage"] = package
    spec = importlib.util.spec_from_file_location("rms_stream_stage.live_stream", Path(__file__).with_name("live_stream.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    changes, frame, install, subscription = module.changes, module.frame, module.install, module.subscription


def replay(data, patches):
    data = copy.deepcopy(data)
    for patch in patches:
        if not patch["path"]:
            data = patch["value"]
            continue
        node = data
        for key in patch["path"][:-1]:
            node = node[key]
        node[patch["path"][-1]] = patch["value"]
    return data


class LiveStreamTests(unittest.TestCase):
    def test_slow_subscription_does_not_block_other_websocket_queries(self):
        application = FastAPI()
        @application.get("/api/fast")
        async def fast():
            return {"ok": True}
        def slow_view(_request):
            time.sleep(0.5)
            return {"users": []}
        install(SimpleNamespace(api=application, os=os, SESSION_COOKIE_NAME="session",
            _verify_session_token=lambda _token: {"id": 1}, manager_dashboard=slow_view))
        with TestClient(application) as client:
            client.cookies.set("session", "valid")
            with client.websocket_connect("/api/live/ws") as ws:
                ws.send_json({"type": "subscribe", "id": "slow", "topic": "dashboard"})
                ws.send_json({"type": "request", "id": "fast", "path": "/api/fast"})
                assert ws.receive_json()["id"] == "fast"
                assert ws.receive_json()["id"] == "slow"

    def test_historical_stream_topics_strip_credentials_and_force_refresh(self):
        for topic in ["performance", "antifraud", "client-live", "markout"]:
            _, params = subscription({"topic": topic, "params": {"selected_groups": ["real"],
                "password": "secret", "server": "private", "login": 1, "refresh_token": 123}})
            assert params == {"selected_groups": ["real"]}

    def test_frontend_and_backend_query_allowlists_match(self):
        try:
            from services.websocket_reads import READ_POST_PATHS
        except ModuleNotFoundError:
            from rms_stream_stage.websocket_reads import READ_POST_PATHS
        frontend = Path(__file__).with_name("readRequests.js")
        if not frontend.exists():
            frontend = Path(__file__).resolve().parents[1] / "frontend/src/lib/readRequests.js"
        section = frontend.read_text(encoding="utf-8").split("new Set([", 1)[1].split("]);", 1)[0]
        self.assertEqual(set(re.findall(r'"(/api/[^\"]+)"', section)), set(READ_POST_PATHS))

    def test_rpc_read_policy_blocks_mutations_and_path_bypasses(self):
        try:
            from services.websocket_reads import read_target
        except ModuleNotFoundError:
            from rms_stream_stage.websocket_reads import read_target
        self.assertEqual(read_target("GET", "/api/calendar?week=1"), ("/api/calendar", "week=1"))
        self.assertEqual(read_target("POST", "/api/reports/deal")[0], "/api/reports/deal")
        for method, path in [("POST", "/api/operations/cases"), ("PUT", "/api/settings/books"),
                ("GET", "/api/auth/session"), ("GET", "//other.invalid/api/calendar"),
                ("GET", "/api/../private"), ("GET", "/api/%2e%2e/private"),
                ("GET", "/api/calendar/bridge.ex5"), ("GET", "/api/reports/job/export")]:
            with self.assertRaises(ValueError, msg=path):
                read_target(method, path)

    def test_rpc_preserves_validation_filters_and_permission_middleware(self):
        from fastapi import Request
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
        application, _ = self.app({"id": 1})
        class Filters(BaseModel):
            login: int

        @application.middleware("http")
        async def guard(request: Request, call_next):
            if request.cookies.get("session") != "valid":
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            if request.url.path.startswith("/api/operations/"):
                return JSONResponse({"detail": "Administrator required"}, status_code=403)
            return await call_next(request)

        @application.post("/api/reports/deal")
        def report(filters: Filters):
            return {"login": filters.login}

        with TestClient(application) as client:
            client.cookies.set("session", "valid")
            with client.websocket_connect("/api/live/ws") as ws:
                for identifier, method, path, body, expected in [
                    ("one", "POST", "/api/reports/deal", {"login": 7}, 200),
                    ("two", "POST", "/api/reports/deal", {"login": "invalid"}, 422),
                    ("three", "GET", "/api/operations/cases", None, 403),
                    ("four", "POST", "/api/operations/cases", {}, 405),
                ]:
                    ws.send_json({"type": "request", "id": identifier, "method": method, "path": path, "body": body})
                    result = ws.receive_json()
                    self.assertEqual((result["type"], result["id"], result["status"]), ("response", identifier, expected))
                    if expected == 200:
                        self.assertEqual(result["data"], {"login": 7})

    def test_rpc_slow_reports_do_not_block_other_replies(self):
        application, _ = self.app({"id": 1})
        @application.get("/api/slow")
        async def slow():
            await asyncio.sleep(0.2)
            return {"source": "slow"}
        @application.get("/api/fast")
        async def fast():
            return {"source": "fast"}
        with TestClient(application) as client:
            client.cookies.set("session", "valid")
            with client.websocket_connect("/api/live/ws") as ws:
                for identifier in ["slow", "fast"]:
                    ws.send_json({"type": "request", "id": identifier, "path": f"/api/{identifier}"})
                self.assertEqual(ws.receive_json()["id"], "fast")
                self.assertEqual(ws.receive_json()["id"], "slow")

    def test_delta_reconstructs_prices_structure_removal_and_null(self):
        before = {"positions": [{"id": 1, "profit": 10}, {"id": 2, "profit": 20}], "status": "connected"}
        for after in [
            {**before, "positions": [{"id": 1, "profit": 11}, {"id": 2, "profit": 20}]},
            {**before, "positions": [{"id": 2, "profit": 20}]},
            {"positions": [], "status": None, "new": True},
        ]:
            self.assertEqual(replay(before, changes(before, after)), after)

    def test_versioned_delta_and_replacement_snapshot(self):
        before = {"positions": [{"id": i, "profit": 10} for i in range(100)]}
        after = copy.deepcopy(before)
        after["positions"][5]["profit"] = 11
        output = frame(before, after, 7)
        self.assertEqual((output["type"], output["base"], output["version"]), ("delta", 7, 8))
        self.assertLess(len(str(output)), len(str(after)) // 5)
        self.assertEqual(frame(before, after, 7, reset=True)["type"], "snapshot")
        self.assertIsNone(frame(before, before, 7))

    def test_subscription_allowlist_validation_and_no_credentials(self):
        topic, params = subscription({"topic": "live-risk", "params": {"selected_groups": ["live"], "password": "secret"}})
        self.assertEqual((topic, params), ("live-risk", {"selected_groups": ["live"]}))
        for message in [{"topic": "write"}, {"topic": "market-watch", "params": {"limit": 1001}},
                        {"topic": "accounts", "params": {"enabled_only": "false"}}]:
            with self.assertRaises(ValueError):
                subscription(message)

    def app(self, user=None):
        application = FastAPI()
        state = SimpleNamespace(rows=[{"Login": 1, "Name": "Client"}])
        namespace = SimpleNamespace(api=application, os=os, SESSION_COOKIE_NAME="session",
            _verify_session_token=lambda token: user if token == "valid" else None,
            manager_accounts=lambda request, **params: {"rows": state.rows, "params": params},
            manager_dashboard=lambda request: {"users": state.rows})
        install(namespace)
        return application, state

    def test_websocket_requires_session_and_rejects_password_change_and_origin(self):
        for user, headers in [(None, {}), ({"id": 1, "must_change_pw": True}, {}),
                              ({"id": 1}, {"origin": "https://untrusted.invalid"})]:
            application, _ = self.app(user)
            with TestClient(application) as client:
                client.cookies.set("session", "valid")
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect("/api/live/ws", headers=headers):
                        pass

    def test_snapshot_reconnect_and_exact_filters(self):
        application, state = self.app({"id": 1})
        with TestClient(application) as client:
            client.cookies.set("session", "valid")
            for expected in [1, 2]:
                state.rows = [{"Login": expected, "Name": "Client"}]
                with client.websocket_connect("/api/live/ws") as ws:
                    ws.send_json({"type": "subscribe", "id": "one", "topic": "accounts",
                                  "params": {"q": str(expected), "enabled_only": True}})
                    output = ws.receive_json()
                    self.assertEqual(output["type"], "snapshot")
                    self.assertEqual(output["data"]["rows"][0]["Login"], expected)
                    self.assertEqual(output["data"]["params"], {"q": str(expected), "enabled_only": True})
                    self.assertEqual(output["version"], 1)

    def test_websocket_delivers_versioned_changes_without_http(self):
        application, state = self.app({"id": 1})
        state.rows = [{"Login": i, "Name": "Client"} for i in range(100)]
        with TestClient(application) as client:
            client.cookies.set("session", "valid")
            with client.websocket_connect("/api/live/ws") as ws:
                ws.send_json({"type": "subscribe", "id": "one", "topic": "dashboard"})
                initial = ws.receive_json()
                state.rows = copy.deepcopy(state.rows)
                state.rows[5]["Name"] = "Updated"
                update = ws.receive_json()
                self.assertEqual(update["type"], "delta")
                self.assertEqual(update["base"], initial["version"])
                self.assertEqual(replay(initial["data"], update["changes"]), {"users": state.rows})


if __name__ == "__main__":
    unittest.main()
