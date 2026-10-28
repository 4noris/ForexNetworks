import copy
import math
import time
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

import api
from services.exposure import build_exposure, ObservedExposureHistory
from services.exposure_api import ExposureRequest
from services.risk_limits import limit_value


def book():
    now=time.time()
    users={i:{"Login":i,"Name":f"Client {i}","Group":"A" if i<3 else "B" if i==3 else "C",
              "Margin":100,"Margin Level %":1000} for i in range(1,5)}
    values=[(1,0,2,100,20,-1),(1,1,.5,120,-10,-.5),(2,0,1,110,5,0),(3,1,3,90,8,0),(4,0,4,50,900,0)]
    positions=[{"Login":login,"Ticket":str(100+n),"Symbol":"XAUUSD" if login<4 else "TEST",
                "Action":side,"Volume":volume,"PriceOpen":price,"PriceCurrent":102,"Profit":profit,"Swap":swap,
                "TimeCreate":now-7*86400 if n==0 else now-100} for n,(login,side,volume,price,profit,swap) in enumerate(values)]
    stamp=__import__('datetime').datetime.fromtimestamp(now,__import__('datetime').timezone.utc).isoformat()
    return {"status":"connected","last_update":now,"errors":[],"groups":["A","B","C"],
            "users_by_login":users,"positions":positions,"group_currencies":{"A":"USD","B":"EUR","C":"USC"},
            "symbol_cache":{"XAUUSD":{"Digits":2}},"tick_cache":{"XAUUSD":{"available":True,"bid":101,"ask":102,"tick_time":stamp}}}


def params(**changes):
    return ExposureRequest(selected_groups=["A","B","C"],**changes).model_dump()


def test_complete_book_reconciles_weighted_prices_signed_lots_and_currencies():
    result=build_exposure(book(),params(expanded_groups=["A","B","C"],expanded_clients=[1]))
    summary=result["summary"]
    assert summary["positions"]==5
    assert summary["clients"]==4
    assert summary["gross_lots"]==10.5
    assert summary["net_lots"]==3.5
    assert summary["floating_pnl"]==13.5
    assert summary["currency"]=="USD"
    assert sum(g["positions"] for g in result["groups"])==5
    assert sum(s["positions"] for s in result["symbols"])==5
    xau=next(s for s in result["symbols"] if s["symbol"]=="XAUUSD")
    assert xau["avg_buy"]==pytest.approx(310/3)
    assert xau["avg_sell"]==pytest.approx(330/3.5)
    assert result["details"]["1"]["total"]==2
    assert result["details"]["1"]["positions"][0]["commission"] is None
    assert result["aged"]["count"]==1
    assert result["aged"]["floating_pnl"]==19
    assert sum(c["positions"] for g in result["groups"] for c in g.get("accounts",[]))==5


def test_group_and_client_opening_averages_are_lot_weighted_by_side():
    cache=book()
    cache["positions"].append(dict(cache["positions"][0],Ticket="999",Volume=1,PriceOpen=130))
    result=build_exposure(cache,params(expanded_groups=["A"]))
    group=next(row for row in result["groups"] if row["group"]=="A")
    assert group["avg_buy"]==pytest.approx((2*100+1*110+1*130)/4)
    assert group["avg_sell"]==120
    client=next(row for row in group["accounts"] if row["login"]=="1")
    assert client["avg_buy"]==pytest.approx((2*100+1*130)/3)
    assert client["avg_sell"]==120
    assert client["price_breakdown"][0]["symbol"]=="XAUUSD"
    cache["positions"][-1]["PriceOpen"]=160
    updated=build_exposure(cache,params(expanded_groups=["A"]))
    assert next(g for g in updated["groups"] if g["group"]=="A")["avg_buy"]==pytest.approx((2*100+110+160)/4)


def test_mixed_symbols_keep_separate_group_client_price_averages():
    cache=book()
    cache["positions"].append(dict(cache["positions"][0],Ticket="999",Symbol="BTCUSD",Volume=1,PriceOpen=10000))
    result=build_exposure(cache,params(expanded_groups=["A"]))
    group=next(row for row in result["groups"] if row["group"]=="A")
    assert group["avg_buy"] is None
    assert group["avg_sell"]==120
    assert {row["symbol"]:row["avg_buy"] for row in group["price_breakdown"]}=={"BTCUSD":10000,"XAUUSD":pytest.approx(310/3)}
    client=next(row for row in group["accounts"] if row["login"]=="1")
    assert client["avg_buy"] is None
    assert {row["symbol"]:row["avg_buy"] for row in client["price_breakdown"]}=={"BTCUSD":10000,"XAUUSD":100}
    filtered=build_exposure(cache,params(symbol="XAUUSD",expanded_groups=["A"]))
    assert filtered["groups"][0]["avg_buy"]==pytest.approx(310/3)


def test_missing_open_price_does_not_turn_into_a_zero_weighted_average():
    cache=book();cache["positions"][0]["PriceOpen"]=None
    group=next(row for row in build_exposure(cache,params())["groups"] if row["group"]=="A")
    assert group["avg_buy"] is None
    assert group["avg_sell"]==120


def test_currency_choice_never_converts_or_combines_unsupported_money():
    result=build_exposure(book(),params(money_currency="USC"))
    assert result["summary"]["floating_pnl"]==900
    assert result["summary"]["gross_lots"]==10.5
    assert next(g for g in result["groups"] if g["group"]=="A")["floating_pnl"] is None


def test_partial_money_and_missing_quote_are_not_synthetic_zeroes():
    cache=book()
    cache["positions"][0]["Profit"]=None
    result=build_exposure(cache,params())
    assert result["summary"]["floating_pnl"] is None
    assert result["summary"]["positions"]==5
    assert "TEST" in result["stale_symbols"]
    assert next(s for s in result["symbols"] if s["symbol"]=="TEST")["bid"] is None


def test_quote_freshness_accounts_for_sdk_second_resolution_only():
    from datetime import datetime,timezone
    cache=book()
    now=time.time()
    cache["tick_cache"]["XAUUSD"]["tick_time"]=datetime.fromtimestamp(now+.5,timezone.utc).isoformat()
    result=build_exposure(cache,params(),quote_now=now)
    assert "XAUUSD" not in result["stale_symbols"]
    cache["tick_cache"]["XAUUSD"]["tick_time"]=datetime.fromtimestamp(now+60,timezone.utc).isoformat()
    assert "XAUUSD" in build_exposure(cache,params(),quote_now=now)["stale_symbols"]


def test_exact_policy_overrides_default_and_filter_does_not_hide_utilization():
    rules=[{"id":1,"enabled":1,"scope_type":"account","scope_value":"*","metric":"net_lots","threshold":1,"warning_pct":70},
           {"id":2,"enabled":1,"scope_type":"account","scope_value":"1","metric":"net_lots","threshold":2,"warning_pct":70},
           {"id":3,"enabled":1,"scope_type":"group","scope_value":"A","metric":"gross_lots","threshold":3,"warning_pct":70}]
    result=build_exposure(book(),params(group="A",direction="SELL",expanded_groups=["A"]),rules)
    group=result["groups"][0]
    assert group["gross_lots"]==.5
    assert group["utilization"]==pytest.approx(3.5/3*100,abs=.01)
    assert group["status"]=="critical"
    client=group["accounts"][0]
    assert client["status"]=="warning"
    assert client["utilization"]==75


def test_group_symbol_search_direction_and_status_scopes_are_exact():
    result=build_exposure(book(),params(group="A",symbol="XAUUSD",query="Client 1",direction="BUY",risk_status="unconfigured"))
    assert result["summary"]["positions"]==1
    assert result["summary"]["buy_lots"]==2
    assert result["summary"]["sell_lots"]==0
    assert result["groups"][0]["group"]=="A"
    assert not result["details"]
    narrowed=params(group="B")
    narrowed["selected_groups"]=["A"]
    assert build_exposure(book(),narrowed)["summary"]["positions"]==0


def test_large_ids_and_position_detail_limits_preserve_real_totals():
    cache=book()
    large=9007199254740993
    cache["users_by_login"][large]=dict(cache["users_by_login"][1],Login=large)
    cache["positions"]=[dict(cache["positions"][0],Login=large,Ticket=str(18446744073709551615-n)) for n in range(600)]
    result=build_exposure(cache,params(expanded_groups=["A"],expanded_clients=[large],detail_limit=10))
    assert result["summary"]["positions"]==600
    assert result["summary"]["gross_lots"]==1200
    assert len(result["details"][str(large)]["positions"])==10
    assert result["details"][str(large)]["total"]==600
    assert result["groups"][0]["accounts"][0]["login"]==str(large)
    assert all(isinstance(row["ticket"],str) for row in result["details"][str(large)]["positions"])
    second=build_exposure(cache,params(expanded_clients=[large],detail_limit=10,detail_offsets={str(large):10}))
    assert second["details"][str(large)]["offset"]==10
    assert not ({r["ticket"] for r in result["details"][str(large)]["positions"]} &
                {r["ticket"] for r in second["details"][str(large)]["positions"]})
    assert second["summary"]["positions"]==600


def test_inactive_account_index_is_never_scanned_or_copied():
    cache=book()
    class IndexedOnly(dict):
        def __iter__(self):raise AssertionError("Do not scan inactive accounts")
        def values(self):raise AssertionError("Do not copy the full book")
        def items(self):raise AssertionError("Do not scan inactive accounts")
    cache["users_by_login"]=IndexedOnly(cache["users_by_login"])
    assert build_exposure(cache,params())["summary"]["positions"]==5


def test_client_pagination_and_global_row_budget_never_reduce_totals():
    cache=book()
    cache["users_by_login"]={i:{"Login":i,"Group":"A","Name":str(i),"Margin":1,"Margin Level %":100} for i in range(1,2106)}
    cache["positions"]=[dict(cache["positions"][0],Login=i,Ticket=str(i)) for i in range(1,2106)]
    first=build_exposure(cache,params(expanded_groups=["A"],clients_limit=100))
    second=build_exposure(cache,params(expanded_groups=["A"],clients_limit=100,client_offsets={"A":100}))
    assert first["summary"]["positions"]==2105
    assert len(first["groups"][0]["accounts"])==100
    assert first["groups"][0]["accounts_total"]==2105
    assert second["groups"][0]["accounts_offset"]==100
    assert not ({r["login"] for r in first["groups"][0]["accounts"]} & {r["login"] for r in second["groups"][0]["accounts"]})


def test_login_location_is_exact_and_unknown_quotes_do_not_change_reported_profit():
    cache=book()
    cache["users_by_login"][11]=dict(cache["users_by_login"][1],Login=11,Name="other")
    cache["positions"].append(dict(cache["positions"][0],Login=11,Ticket="999"))
    result=build_exposure(cache,params(query="1",expanded_groups=["A"]))
    assert result["summary"]["clients"]==1
    assert result["located_account"]["login"]=="1"
    assert result["located_account"]["visible"]
    assert result["summary"]["floating_pnl"]==8.5


def test_api_recovers_with_warming_state_when_manager_reconnects(monkeypatch):
    monkeypatch.setattr(api,"get_configured_manager",lambda:(_ for _ in ()).throw(RuntimeError("test disconnect")))
    response=api.get_exposure(ExposureRequest(selected_groups=["A"]),SimpleNamespace(state=SimpleNamespace(user={"id":1})))
    assert response["status"]=="warming"
    assert response["summary"]=={}


def test_net_policy_persists_audits_and_rejects_operator_writes(monkeypatch,tmp_path):
    from fastapi import HTTPException
    monkeypatch.setattr(api,"DB_PATH",tmp_path/"policy.db")
    api.init_db()
    admin=SimpleNamespace(state=SimpleNamespace(user={"id":1,"username":"fixture-admin","is_admin":True}))
    operator=SimpleNamespace(state=SimpleNamespace(user={"id":2,"username":"fixture-operator","is_admin":False}))
    request=api.ExposureLimitRequest(scope_type="account",scope_value="*",metric="net_lots",threshold=12,warning_pct=75)
    created=api.create_limit(request,admin)
    assert created["metric"]=="net_lots"
    assert created["threshold"]==12
    assert any(row["id"]==created["id"] for row in api.list_limits()["limits"])
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='limit.created'").fetchone()[0]==1
    with pytest.raises(HTTPException) as failure:
        api.create_limit(request,operator)
    assert failure.value.status_code==403


def test_exposure_stream_route_is_a_read_and_policy_writes_are_not_rpc():
    from services.live_stream import subscription
    from services.websocket_reads import read_target
    assert read_target("POST","/api/risk/exposure")==("/api/risk/exposure","")
    topic,params=subscription({"topic":"exposure","params":{"selected_groups":["A"],"password":"not-a-real-secret"}})
    assert topic=="exposure"
    assert "password" not in params
    for method,path in (("POST","/api/operations/limits"),("PUT","/api/operations/limits/1"),("DELETE","/api/operations/limits/1")):
        with pytest.raises(ValueError):read_target(method,path)


def test_delayed_book_retains_verified_values_and_empty_book_is_real_zero():
    cache=book();cache["last_update"]-=20
    result=build_exposure(cache,params())
    assert result["status"]=="delayed"
    assert result["summary"]["positions"]==5
    cache["last_update"]=time.time();cache["positions"]=[]
    assert build_exposure(cache,params())["summary"]["positions"]==0
    cache["status"]="disconnected"
    assert build_exposure(cache,params())["summary"]=={}


def test_history_has_no_backfilled_points_and_is_bounded_per_scope():
    history=ObservedExposureHistory(max_scopes=2)
    def point(t):return {"source_ms":t,"net_lots":1,"gross_lots":2,"floating_pnl":3}
    rows,since=history.observe("a",point(100000))
    assert rows==[point(100000)]
    assert since==100000
    rows,_=history.observe("a",point(100500))
    assert rows==[point(100500)]
    rows,_=history.observe("a",point(102000),record=False)
    assert rows==[point(100500)]
    for n in range(3000):history.observe("a",point(100000+n*1000))
    assert len(history.scopes["a"])<=2400
    assert len(history.observe("a",point(3100000))[0])<=240
    history.observe("b",point(3100000));history.observe("c",point(3100000))
    assert list(history.scopes)==["b","c"]


def test_wildcard_net_limits_do_not_offset_unrelated_clients():
    accounts=[{"login":1,"group":"A"},{"login":2,"group":"A"}]
    positions=[{"login":1,"symbol":"X","action":"BUY","volume":10},
               {"login":2,"symbol":"X","action":"SELL","volume":15}]
    limit={"scope_type":"account","scope_value":"*","metric":"net_lots"}
    assert limit_value(limit,accounts,positions)[0]==15
    assert api.ExposureLimitRequest(scope_type="account",scope_value="*",metric="net_lots",threshold=10)
    with pytest.raises(ValueError):api.ExposureLimitRequest(scope_type="account",scope_value="*",metric="floating_loss",threshold=10)


def test_api_uses_native_generation_and_separates_history_by_user(monkeypatch,tmp_path):
    from services import exposure_api
    cache=book()
    @contextmanager
    def generation():yield cache
    manager=SimpleNamespace(server="test-only",manager_login=1,_cache_generation=generation)
    monkeypatch.setattr(api,"get_configured_manager",lambda:manager)
    monkeypatch.setattr(api,"DB_PATH",tmp_path/"exposure.db")
    monkeypatch.setattr(exposure_api,"HISTORY",ObservedExposureHistory())
    api.init_db()
    first=SimpleNamespace(state=SimpleNamespace(user={"id":1}))
    second=SimpleNamespace(state=SimpleNamespace(user={"id":2}))
    request=ExposureRequest(selected_groups=["A"])
    result=api.get_exposure(request,first)
    assert result["summary"]["positions"]==3
    cache["last_update"]+=2
    api.get_exposure(request,first)
    assert len(api.get_exposure(request,second)["history"])==1
    assert len(api.get_exposure(request,first)["history"])==2
