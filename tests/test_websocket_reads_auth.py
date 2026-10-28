"""Exercise the real RMS middleware through the WebSocket query adapter."""
from fastapi.testclient import TestClient

import api


def test_websocket_data_queries_keep_real_http_auth_permissions_and_validation(monkeypatch):
    monkeypatch.setenv("MT5_MANAGER_AUTOCONNECT", "false")
    user = {"id": 123, "username": "test-viewer", "is_admin": False, "must_change_pw": False}
    monkeypatch.setattr(api, "_verify_session_token", lambda token: user if token == "test-valid" else None)
    with TestClient(api.api) as client:
        client.cookies.set(api.SESSION_COOKIE_NAME, "test-valid")
        with client.websocket_connect("/api/live/ws") as ws:
            for identifier, method, path, body, expected in [
                ("health", "GET", "/api/health", None, 200),
                ("permission", "GET", "/api/operations/cases", None, 403),
                ("validation", "POST", "/api/risk/live", {"account_login": -1}, 422),
                ("write", "POST", "/api/settings/reset-database", {}, 405),
            ]:
                ws.send_json({"type": "request", "id": identifier, "method": method, "path": path, "body": body})
                response = ws.receive_json()
                assert (response["type"], response["id"], response["status"]) == ("response", identifier, expected)
            # Each dispatched read rechecks the original session, independently
            # of the streaming connection's periodic authentication check.
            monkeypatch.setattr(api, "_verify_session_token", lambda _token: None)
            ws.send_json({"type": "request", "id": "expired", "path": "/api/settings/books"})
            assert ws.receive_json()["status"] == 401
