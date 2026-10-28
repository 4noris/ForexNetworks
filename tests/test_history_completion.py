import json
import time
import uuid

from services.historical import HistoryStore
from services.live_scope import scope_live_payload


def test_coverage_does_not_bridge_unfetched_backfill_gap(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    store.migrate("2026-01-01")
    job = store.enqueue()
    store.save_batch(job,"real",1000,2000,[])
    store.save_batch(job,"real",0,99,[])
    assert store.checkpoint("real")["coverage_from"] == 1000
    assert store.backfill_start("real",0) == 100
    store.save_batch(job,"real",100,999,[])
    assert store.checkpoint("real")["coverage_from"] == 0
    assert store.checkpoint("real")["through_ts"] == 2000


def test_export_owner_cannot_publish_after_lease_takeover(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    store.migrate("2026-01-01")
    export_id = uuid.uuid4().hex
    with store.connect() as con:
        con.execute("INSERT INTO history_exports(id,status,actor,format,filters,created_at) VALUES(?,'queued','admin','csv','{}',?)",(export_id,time.time()))
    assert store.lease("history-export","one")
    assert store.claim_export("one")["id"] == export_id
    assert store.claim_export("two") is None
    with store.connect() as con:
        con.execute("UPDATE history_leases SET expires=0")
        con.execute("UPDATE history_exports SET lease_until=0")
    assert store.lease("history-export","two")
    assert store.claim_export("two")["id"] == export_id
    assert not store.finish_export(export_id,"one",b"old")
    assert store.finish_export(export_id,"two",b"new")


def test_live_symbol_and_risk_scope_uses_matching_positions_only():
    accounts = [{"login":1,"severity":"high","currency":"USD","balance":100,"equity":120,"margin":10,"margin_free":110,"floating_pnl":20},
                {"login":2,"severity":"normal","currency":"EUR","balance":500,"equity":500,"margin":0,"margin_free":500,"floating_pnl":0}]
    positions = [{"login":1,"symbol":"EURUSD","volume":2,"profit":5,"action":"BUY"},
                 {"login":1,"symbol":"GBPUSD","volume":3,"profit":15,"action":"SELL"},
                 {"login":2,"symbol":"EURUSD","volume":1,"profit":0,"action":"SELL"}]
    result = scope_live_payload({"accounts":accounts,"positions":positions,"summary":{"high_lot_threshold":1},"warnings":[]},symbol="EURUSD",risk_category="high")
    assert result["summary"]["floating_pnl"] == 5
    assert result["summary"]["gross_lots"] == 2
    assert result["summary"]["equity"] == 120
    assert result["summary"]["currencies"] == ["USD"]
    assert len(result["positions"]) == 1


def test_snapshot_risk_signals_persist_evidence_and_thresholds(tmp_path):
    import api
    from unittest.mock import patch
    path = tmp_path / "signals.db"
    with patch.object(api,"DB_PATH",str(path)):
        api.init_db()
    store = HistoryStore(path)
    store.migrate("2026-01-01")
    now = time.time()
    user = {"Login":1,"Name":"Test","Group":"real","Currency":"USD","Balance":1000,"Equity":1000,"Margin":10,"Free Margin":990,"Floating PnL":0}
    position = {"Ticket":10,"Login":1,"Symbol":"EURUSD","Action":0,"Volume":1,"Profit":0}
    store.snapshots({"last_update":now-10,"users":[user],"positions":[position]})
    store.snapshots({"last_update":now,"users":[{**user,"Margin":900,"Floating PnL":300}],"positions":[{**position,"Volume":3}]})
    with store.connect() as con:
        events = con.execute("SELECT detector,evidence_json,thresholds_json FROM risk_events WHERE event_type='historical_snapshot'").fetchall()
    assert {r[0] for r in events} == {"observed_margin_utilization","observed_floating_pnl_change","observed_symbol_exposure_increase"}
    assert all(json.loads(r[1])["observed_at"] for r in events)
    assert all(json.loads(r[2])["enabled"] for r in events)
