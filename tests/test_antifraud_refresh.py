import hashlib
import json
from contextlib import contextmanager

import api
import mt5_connection


def _cache_key(request):
    return api._antifraud_cache_key(request)


def test_antifraud_without_selected_groups_never_returns_cached_data(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "antifraud-empty.db")
    api.init_db()
    payload = api._empty_antifraud_snapshot(None)
    payload["summary"]["deals_scanned"] = 99
    with api._conn() as con:
        con.execute(
            "INSERT INTO antifraud_snapshots(cache_key,payload_json) VALUES (?,?)",
            ("unrelated-manager", json.dumps(payload)),
        )

    result = api.get_antifraud_snapshot(api.AntiFraudRequest())

    assert result["summary"]["deals_scanned"] == 0
    assert result["status"] == "offline"
    assert "Select one or more groups" in result["warnings"][0]


def test_persisted_antifraud_snapshot_starts_background_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "antifraud.db")
    api.init_db()
    request = api.AntiFraudRequest(selected_groups=["real"])
    key = _cache_key(request)
    payload = api._empty_antifraud_snapshot(None)
    payload["summary"]["deals_scanned"] = 1
    with api._conn() as con:
        con.execute(
            "INSERT INTO antifraud_snapshots(cache_key,payload_json) VALUES (?,?)",
            (key, json.dumps(payload)),
        )

    started = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(self.target)

    api._ANTIFRAUD_CACHE.clear()
    api._ANTIFRAUD_REFRESHING.clear()
    monkeypatch.setattr(api.threading, "Thread", FakeThread)

    result = api.get_antifraud_snapshot(request)

    assert result["cache"]["persistent"] is True
    assert result["cache"]["refreshing"] is True
    assert isinstance(result["cache"]["age_seconds"], int)
    assert len(started) == 1
    assert key in api._ANTIFRAUD_REFRESHING
    api._ANTIFRAUD_CACHE.clear()
    api._ANTIFRAUD_REFRESHING.clear()


def test_refresh_token_bypasses_fresh_antifraud_memory_cache(monkeypatch):
    request = api.AntiFraudRequest(selected_groups=["real"], refresh_token=1)
    key = _cache_key(request)
    cached = api._empty_antifraud_snapshot(None)
    cached["status"] = "cached-marker"
    api._ANTIFRAUD_CACHE[key] = (api.time_module.monotonic(), cached)
    monkeypatch.setattr(api, "get_configured_manager", lambda: None)

    result = api.get_antifraud_snapshot(request)

    assert result["status"] == "warming"
    assert result["status"] != "cached-marker"
    api._ANTIFRAUD_CACHE.clear()


def test_focused_antifraud_scans_without_full_live_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "antifraud-focused.db")
    api.init_db()
    requested_groups = ["real\\desk\\a", "real\\desk\\b"]
    captured = {}

    class FakeManager:
        is_connected = True
        manager_login = 2076

        def live_risk_cache_view(self, groups=None):
            assert groups == []
            return {
                "users": [],
                "positions": [],
                "groups": requested_groups,
            }

    @contextmanager
    def fake_session(*_args):
        yield object()

    def fake_build(_handle, groups, _excluded, **kwargs):
        captured["groups"] = groups
        captured["request_groups"] = kwargs["request_groups"]
        captured["cached_users"] = kwargs["cached_users"]
        result = api._empty_antifraud_snapshot(None)
        result["selected_groups"] = groups
        result["summary"]["deals_scanned"] = 1
        return result

    api._ANTIFRAUD_CACHE.clear()
    api._ANTIFRAUD_REFRESHING.clear()
    monkeypatch.setattr(api, "get_configured_manager", lambda: FakeManager())
    monkeypatch.setattr(api, "market_manager_session", fake_session)
    monkeypatch.setattr(api, "build_antifraud_snapshot", fake_build)
    monkeypatch.setattr(api, "notify_new_antifraud_events", lambda *_args: None)

    result = api.get_antifraud_snapshot(api.AntiFraudRequest(
        selected_groups=requested_groups,
        refresh_token=1,
    ))

    assert result["summary"]["deals_scanned"] == 1
    assert captured == {
        "groups": requested_groups,
        "request_groups": ["real\\*"],
        "cached_users": None,
    }
    api._ANTIFRAUD_CACHE.clear()
