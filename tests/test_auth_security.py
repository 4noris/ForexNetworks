from __future__ import annotations

import json
import tempfile
import gc
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

import api


def _request(user=None) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1000)})
    if user is not None:
        request.state.user = user
    return request


def test_login_sets_http_only_persistent_cookie_and_password_change_is_enforced():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "auth.db"
        with patch.object(api, "DB_PATH", str(database)):
            api.init_db()
            response = api.login(api.LoginRequest(username="admin", password="admin"), _request())
            payload = json.loads(response.body)
            assert payload["user"]["must_change_pw"] == 1
            cookie = response.headers["set-cookie"]
            assert api.SESSION_COOKIE_NAME in cookie
            assert "HttpOnly" in cookie
            assert "Max-Age=" in cookie

            changed = api.change_password(
                api.PasswordChangeRequest(current_password="admin", new_password="A-unique-production-passphrase"),
                _request(payload["user"]),
            )
            changed_payload = json.loads(changed.body)
            assert changed_payload["user"]["must_change_pw"] == 0
            assert api.verify_user("admin", "admin") is None
            assert api.verify_user("admin", "A-unique-production-passphrase") is not None
        gc.collect()


def test_legacy_mt5_profile_routes_are_not_registered():
    paths = {getattr(route, "path", "") for route in api.api.routes}
    assert not any(path.startswith("/api/mt5/profiles") for path in paths)


def test_copy_trading_routes_are_not_registered():
    paths = {getattr(route, "path", "") for route in api.api.routes}
    assert not any(path.startswith("/api/copy-trading") for path in paths)


def test_cookie_session_slides_and_logout_does_not_reissue_it(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "sliding-session.db"
        monkeypatch.setenv("MT5_MANAGER_AUTOCONNECT", "false")
        with patch.object(api, "DB_PATH", str(database)):
            api.init_db()
            with TestClient(api.api) as client:
                login_response = client.post(
                    "/api/auth/login", json={"username": "admin", "password": "admin"}
                )
                assert login_response.status_code == 200
                assert "access_token" not in login_response.json()

                session_response = client.get("/api/auth/session")
                assert session_response.status_code == 200
                assert api.SESSION_COOKIE_NAME in session_response.headers.get("set-cookie", "")

                logout_response = client.post("/api/auth/logout")
                assert logout_response.status_code == 200
                cookie_header = logout_response.headers.get("set-cookie", "")
                assert "Max-Age=0" in cookie_header
                assert cookie_header.count(api.SESSION_COOKIE_NAME) == 1
        gc.collect()


def test_non_admin_cannot_read_privileged_operational_apis(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "authorization.db"
        monkeypatch.setenv("MT5_MANAGER_AUTOCONNECT", "false")
        with patch.object(api, "DB_PATH", str(database)):
            api.init_db()
            with api._conn() as con:
                con.execute(
                    "INSERT INTO app_users (username, password_hash, is_admin, must_change_pw) VALUES (?,?,0,0)",
                    ("viewer", api._hash_pw("A-unique-viewer-passphrase")),
                )

            with TestClient(api.api) as client:
                login_response = client.post(
                    "/api/auth/login",
                    json={"username": "viewer", "password": "A-unique-viewer-passphrase"},
                )
                assert login_response.status_code == 200

                for path in (
                    "/api/operations/cases",
                    "/api/risk-automation/overview",
                    "/api/notifications/telegram/status",
                ):
                    response = client.get(path)
                    assert response.status_code == 403, path
                    assert response.json()["detail"] == "Administrator permission is required"
        gc.collect()
