import math

import api
from services.live_money import currency_money_totals
from services.live_scope import scope_live_payload


def user(login,currency,equity=100):
    return {"Login":login,"Currency":currency,"Group":"test-only","Name":"fixture",
            "Balance":90,"Equity":equity,"Margin":5,"Free Margin":equity-5,
            "Margin Level %":equity/5*100,"Floating PnL":10,"Live Floating PnL":10}


def test_real_currency_totals_are_separate_including_cent_units():
    rows=[user(1,"USD"),user(2,"USC",10000),user(3,"USD",200)]
    totals=currency_money_totals(rows,native=True)
    assert totals["USD"]["equity"]==300
    assert totals["USC"]["equity"]==10000
    assert totals["USD"]["accounts"]==2
    assert totals["USD"]["floating_pnl"]==20


def test_unknown_currency_or_missing_money_is_not_zero_or_invented_conversion():
    rows=[user(1,""),user(2,"USD")]
    rows[1]["Equity"]=None
    totals=currency_money_totals(rows,native=True)
    assert totals["UNKNOWN"]["equity"] is None
    assert totals["USD"]["equity"] is None
    assert totals["USD"]["balance"]==90
    rows[1]["Equity"]=math.inf
    assert currency_money_totals(rows,native=True)["USD"]["equity"] is None


def test_currency_totals_cover_full_scope_before_500_row_limit(monkeypatch):
    monkeypatch.setattr(api,"enrich_ip_addresses_nonblocking",lambda ips:{})
    rows=[user(login,"USD" if login<=600 else "USC") for login in range(1,1101)]
    positions=[{"Login":login,"Symbol":"TEST_ONLY","Ticket":str(login),"Action":0,
                "Volume":1,"Profit":10,"Swap":0} for login in range(1,1101)]
    result=api.build_live_risk_snapshot_from_cache(rows,positions,["test-only"],row_limit=500)
    assert len(result["accounts"])==500
    assert result["summary"]["currency_totals"]["USD"]["accounts"]==600
    assert result["summary"]["currency_totals"]["USD"]["equity"]==60000
    assert result["summary"]["currency_totals"]["USC"]["equity"]==50000
    assert all(row["currency"] in ("USD","USC") for row in result["accounts"])


def test_account_and_symbol_filters_recompute_currency_scope(monkeypatch):
    monkeypatch.setattr(api,"enrich_ip_addresses_nonblocking",lambda ips:{})
    rows=[user(1,"USD"),user(2,"USC",10000)]
    positions=[{"Login":login,"Symbol":"TEST_ONLY","Ticket":str(login),"Action":0,
                "Volume":1,"Profit":10,"Swap":0} for login in (1,2)]
    result=api.build_live_risk_snapshot_from_cache(rows,positions,["test-only"],row_limit=None)
    result=scope_live_payload(result,account_login=2,symbol="TEST_ONLY")
    assert result["summary"]["currency_totals"]=={"USC":{"accounts":1,"balance":90,"equity":10000,
                                                          "margin":5,"margin_free":9995,"floating_pnl":10}}
