from __future__ import annotations

import os
import gc
import tempfile
import threading
import urllib.error
from pathlib import Path
from unittest.mock import patch

import api


def test_google_auto_sync_preserves_export_when_mt5_scope_is_empty(monkeypatch):
    class Manager:
        api = object()
        api_lock = threading.Lock()
        is_connected = True

        def live_risk_cache_view(self, groups=None):
            return {"groups": ["live"]}

    monkeypatch.setenv("GOOGLE_SHEETS_AUTO_SYNC_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_SHEETS_ENABLED", "true")
    monkeypatch.setattr("mt5_connection.get_manager", lambda: Manager())
    monkeypatch.setattr(api, "_google_sheets_auto_sync_scope", lambda _manager: ["live"])
    monkeypatch.setattr(api, "build_live_risk_snapshot", lambda *_args, **_kwargs: {"summary": {"scoped_accounts": 0}})
    monkeypatch.setattr(api, "_google_daily_leaders", lambda *_args: [])
    monkeypatch.setattr(api, "_sync_google_sheet", lambda *_args: (_ for _ in ()).throw(AssertionError("empty scope must not overwrite export")))

    assert api._run_google_sheets_auto_sync() is False


def test_log_pruning_only_deletes_stale_application_launcher_logs(tmp_path: Path):
    stale_app_log = tmp_path / "risk-backend.err.log"
    fresh_app_log = tmp_path / "atomic-backend.out.log"
    broker_journal = tmp_path / "20260714.log"
    unrelated_log = tmp_path / "service.log"
    for path in (stale_app_log, fresh_app_log, broker_journal, unrelated_log):
        path.write_text("log", encoding="utf-8")

    now = 100_000.0
    os.utime(stale_app_log, (now - 7200, now - 7200))
    os.utime(broker_journal, (now - 7200, now - 7200))
    os.utime(unrelated_log, (now - 7200, now - 7200))
    os.utime(fresh_app_log, (now, now))

    deleted = api._prune_application_logs(
        [tmp_path], now=now, retention_seconds=3600
    )

    assert deleted == [stale_app_log]
    assert not stale_app_log.exists()
    assert fresh_app_log.exists()
    assert broker_journal.exists()
    assert unrelated_log.exists()


def test_mt5_journal_pruning_retains_recent_and_non_journal_logs(tmp_path: Path):
    expired_journal = tmp_path / "20260601.log"
    current_journal = tmp_path / "20260715.log"
    unrelated_log = tmp_path / "service.log"
    for path in (expired_journal, current_journal, unrelated_log):
        path.write_text("log", encoding="utf-8")

    now = 2_000_000.0
    os.utime(expired_journal, (now - 8 * 86400, now - 8 * 86400))
    os.utime(current_journal, (now - 60, now - 60))
    os.utime(unrelated_log, (now - 8 * 86400, now - 8 * 86400))

    deleted = api._prune_mt5_journals(
        [tmp_path], now=now, retention_seconds=7 * 86400
    )

    assert deleted == [expired_journal]
    assert not expired_journal.exists()
    assert current_journal.exists()
    assert unrelated_log.exists()


def test_cache_maintenance_preserves_unexpired_safety_entries(monkeypatch):
    class FakeMarketEngine:
        def clear_transient_cache(self):
            return 3

    monkeypatch.setattr("mt5_connection.get_market_engine", lambda: FakeMarketEngine())
    api._PERFORMANCE_CACHE[("report",)] = (1.0, {"rows": [1]})
    api._IP_INTEL_CACHE.update({
        "expired": (50.0, {}),
        "current": (150.0, {}),
    })
    api._TELEGRAM_SENT.update({
        "expired": 0.0,
        "current": 99.0,
    })

    cleared = api._clear_runtime_caches(now=100.0)

    assert cleared == {
        "performance": 1,
        "market_watch": 3,
        "expired_ip": 1,
        "expired_alert_dedupe": 0,
    }
    assert api._PERFORMANCE_CACHE == {}
    assert set(api._IP_INTEL_CACHE) == {"current"}
    assert "current" in api._TELEGRAM_SENT


def test_ip_intelligence_stops_batching_after_provider_failure(monkeypatch):
    ips = [f"8.8.{index // 250}.{index % 250 + 1}" for index in range(201)]
    calls = []

    def fail_request(request, timeout):
        calls.append((request, timeout))
        raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, None)

    api._IP_INTEL_CACHE.clear()
    monkeypatch.setattr(api, "_IP_INTEL_BACKOFF_UNTIL", 0.0)
    monkeypatch.setattr(api.urllib.request, "urlopen", fail_request)

    result = api.enrich_ip_addresses(ips)

    assert len(result) == len(ips)
    assert len(calls) == 1


def test_runtime_maintenance_prunes_only_expired_audit_rows(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "maintenance.db"
        with patch.object(api, "DB_PATH", str(database)):
            api.init_db()
            with api._conn() as con:
                con.execute(
                    "INSERT INTO audit_log (actor,action,created_at) VALUES (?,?,datetime('now','-120 days'))",
                    ("system", "expired"),
                )
                con.execute(
                    "INSERT INTO audit_log (actor,action,created_at) VALUES (?,?,datetime('now'))",
                    ("system", "current"),
                )
            monkeypatch.setattr(api, "_clear_runtime_caches", lambda: {})
            monkeypatch.setattr(api, "_prune_application_logs", lambda: [])
            monkeypatch.setattr(api, "_prune_mt5_journals", lambda: [])

            result = api._run_runtime_maintenance()

            with api._conn() as con:
                actions = [row[0] for row in con.execute("SELECT action FROM audit_log").fetchall()]
            assert result["deleted_audit_rows"] == 1
            assert actions == ["current"]
        gc.collect()
