from services.risk_limits import limit_value


def account(login=1,currency="USD",**changes):
    return {"login":login,"group":"real","currency":currency,"floating_pnl":-20,"equity":0,"margin":100,"concentration_pct":80,**changes}


def test_notional_limit_requires_real_valuation_and_preserves_zero():
    limit={"scope_type":"symbol","scope_value":"EURUSD","metric":"gross_exposure"}
    pos={"login":1,"symbol":"EURUSD","volume":2,"price_current":1.2,"notional":None}
    assert limit_value(limit,[account()],[pos])[0] is None
    assert limit_value(limit,[account()],[{**pos,"notional":240000}])[0] == 240000
    assert limit_value(limit,[account()],[{**pos,"notional":0}])[0] == 0


def test_mixed_currency_limit_cannot_sum_nominal_money():
    limit={"scope_type":"group","scope_value":"real","metric":"floating_loss"}
    assert limit_value(limit,[account(),account(2,"EUR")],[])[0] is None
    assert limit_value(limit,[account(),account(2)],[])[0] == 40


def test_zero_and_negative_equity_are_valid_margin_breaches():
    limit={"scope_type":"account","scope_value":"1","metric":"margin_level_below"}
    assert limit_value(limit,[account()],[])[0] == 0
    assert limit_value(limit,[account(equity=-10)],[])[0] == -10
    assert limit_value(limit,[account(margin=0)],[])[0] is None


def test_lot_limit_uses_complete_exact_scope():
    limit={"scope_type":"account","scope_value":"1","metric":"gross_lots"}
    positions=[{"login":1,"symbol":"EURUSD","volume":0.1} for _ in range(601)]
    positions.append({"login":2,"symbol":"EURUSD","volume":999})
    assert limit_value(limit,[account(),account(2)],positions)[0] == 60.1


def test_limit_update_is_validated_persisted_and_audited(tmp_path,monkeypatch):
    import api
    from fastapi import Request,HTTPException
    import pytest
    monkeypatch.setattr(api,"DB_PATH",str(tmp_path/"config.db"))
    api.init_db()
    request=Request({"type":"http","method":"PUT","path":"/","headers":[]})
    request.state.user={"username":"admin-test","is_admin":True}
    original=api.create_limit(api.ExposureLimitRequest(scope_type="account",scope_value="1",metric="gross_lots",threshold=10),request)
    result=api.update_limit(original["id"],api.ExposureLimitRequest(scope_type="account",scope_value="1",metric="gross_lots",threshold=20,enabled=False),request)
    assert result["threshold"] == 20
    assert result["enabled"] == 0
    with api._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='limit.updated'").fetchone()[0] == 1
    request.state.user["is_admin"]=False
    with pytest.raises(HTTPException) as exc:
        api.update_limit(original["id"],api.ExposureLimitRequest(scope_type="account",scope_value="1",metric="gross_lots",threshold=20),request)
    assert exc.value.status_code == 403


def test_data_health_requires_admin_and_reports_real_stale_mappings(tmp_path,monkeypatch):
    import api
    import mt5_connection
    import time
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    path=str(tmp_path/"health.db")
    monkeypatch.setattr(api,"DB_PATH",path)
    api.init_db()
    store=api._HISTORY_WORKER.store
    monkeypatch.setattr(store,"path",path)
    store.migrate("2026-01-01")
    with store.connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS risk_approval_queue(id INTEGER PRIMARY KEY,status TEXT)")
        con.execute("UPDATE book_group_settings SET b_book_json=? WHERE id=1",('["obsolete"]',))
    manager=SimpleNamespace(is_connected=True,group_cache_view=lambda:["live"],update_marker=lambda:time.time()-1)
    monkeypatch.setattr(mt5_connection,"get_manager",lambda:manager)
    user={"id":1,"username":"tester","is_admin":True,"must_change_pw":False}
    monkeypatch.setattr(api,"_verify_session_token",lambda _:user)
    client=TestClient(api.api)
    response=client.get("/api/dealing-risk/health",headers={"Authorization":"Bearer test"})
    assert response.status_code == 200,response.text
    assert response.json()["group_mismatches"] == [{"book":"b_book","group":"obsolete"}]
    assert response.json()["stale"] is False
    user["is_admin"]=False
    assert client.get("/api/dealing-risk/health",headers={"Authorization":"Bearer test"}).status_code == 403
    client.close()
