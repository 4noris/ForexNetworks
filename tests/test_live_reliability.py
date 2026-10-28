import time
from types import SimpleNamespace

import api
import mt5_connection


def test_empty_groups_use_fresh_complete_generation_without_sdk_scan(monkeypatch):
    view = {"users":[],"positions":[],"groups":["empty"],"last_update":time.time(),"errors":[]}
    manager = SimpleNamespace(is_connected=True,server="test",manager_login=1,live_risk_cache_view=lambda **kwargs:view)
    monkeypatch.setattr(api,"get_configured_manager",lambda:manager)
    monkeypatch.setattr(mt5_connection,"FULL_SNAPSHOT_ENABLED",True)
    monkeypatch.setattr(api,"notify_live_risk_events",lambda *args:None)
    monkeypatch.setattr(api,"_apply_live_risk_notices",lambda payload:payload)
    monkeypatch.setattr(api,"build_live_risk_snapshot",lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError("No native scan needed")))
    result = api.get_live_risk(api.LiveRiskRequest(selected_groups=["empty"]))
    assert result["cache"]["source"] == "manager"
    assert result["status"] == "connected"
    assert result["summary"]["open_positions"] == 0
    assert result["summary"]["equity"] == 0


def test_offline_response_never_claims_fresh_zero_balances():
    result = api._empty_live_risk_snapshot("Network connection failed")
    assert result["status"] == "offline"
    assert result["summary"]["equity"] is None
    assert result["summary"]["floating_pnl"] is None
    assert result["source_updated_at"] is None
    assert result["generated_at"] is None
    assert result["warnings"]


def test_group_currency_does_not_erase_authoritative_user_currency():
    manager=mt5_connection.MT5ConnectionManager("test",1,"not-used")
    manager.cache["users"]=[{"Login":1,"Group":"real","Currency":"EUR"}]
    assert manager.live_risk_cache_view()["users"][0]["Currency"] == "EUR"


def test_account_snapshot_latest_page_uses_one_observed_generation(tmp_path):
    from services.historical import HistoryStore
    store=HistoryStore(tmp_path/"history.db")
    store.migrate("2026-01-01")
    with store.connect() as con:
        con.executemany("INSERT INTO history_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",[
            (10,1,"real","USD","10","11","1","10","1",1),
            (20,1,"real","USD","10","12","1","11","2",1),
            (20,2,"real","USD","20","22","2","20","2",1),
        ])
    result=store.snapshot_report({"from_date":"1970-01-01","to_date":"1970-01-01"},dimension="login",latest_only=True)
    assert result["total"] == 2
    assert {r["source_ts"] for r in result["rows"]} == {20}


def test_transport_cleanup_handler_only_ignores_expected_windows_disconnect():
    delegated=[]
    loop=SimpleNamespace(default_exception_handler=lambda context:delegated.append(context))
    disconnect=ConnectionResetError("Peer closed")
    disconnect.winerror=10054
    api._runtime_loop_exception_handler(loop,{"exception":disconnect,"message":"Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)"})
    assert not delegated
    api._runtime_loop_exception_handler(loop,{"exception":ValueError("Real bug"),"message":"Unhandled error"})
    assert len(delegated) == 1


def test_numeric_cached_ip_matches_raw_sdk_ip():
    assert api._ip_text("134744072") == api._ip_text(134744072) == "8.8.8.8"
    assert api._ip_text("8.8.8.8") == "8.8.8.8"
    assert api._ip_text("invalid") == ""


def test_group_refresh_does_not_drop_published_user_memberships(monkeypatch):
    manager=mt5_connection.MT5ConnectionManager("test",1,"not-used")
    manager.cache["users"]=[{"Login":1,"Group":"real\\request-only"}]
    monkeypatch.setattr(manager,"_fetch_group_catalog",lambda **kwargs:["real\\pump"])
    manager._refresh_group_cache()
    assert manager.group_cache_view() == ["real\\pump","real\\request-only"]


def test_verified_group_currency_repair_rebuilds_totals_without_duplicates(tmp_path):
    from services.historical import HistoryStore
    from services.history_worker import normalize
    store=HistoryStore(tmp_path/"history.db")
    store.migrate("2026-01-01")
    row=normalize({"Deal":1,"Login":10,"Time":100,"Symbol":"EURUSD","Action":0,"Entry":0,"Profit":1,"Commission":0,"Storage":0,"Fee":0,"Volume":10000},
                  "deal",{10:{"currency":""}},"real",lambda r,k,default=None:r.get(k,default))
    store.save_batch(store.enqueue(),"real",0,200,[row])
    assert store.resolve_group_currency("real","EUR") == 1
    report=store.report({"from_date":"1970-01-01","to_date":"1970-01-01"})
    assert report["totals"][0]["currency"] == "EUR"
    assert report["totals"][0]["deals"] == 1
    assert store.resolve_group_currency("real","EUR") == 0
