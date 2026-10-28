import math

import pytest


from services.native_totals import NativeAccountTotals


def row(login, balance=1000.0, equity=1010.0, margin=5.0, floating=10.0):
    return {"Login":login,"Name":"fixture","Group":"fixture","Enabled":True,
            "Balance":balance,"Equity":equity,"Margin":margin,"Free Margin":equity-margin,
            "Floating PnL":floating,"Live Floating PnL":floating,"Margin Level %":equity / margin * 100 if margin else 0}


def test_repeated_active_cycles_preserve_exact_totals_and_previous_published_metrics():
    totals=NativeAccountTotals()
    rows={str(i):row(i,balance=1e16 if i==1 else -1e16 if i==2 else 1000.01) for i in range(1,1601)}
    for key,value in rows.items(): totals.replace(key,None,value)
    previous=totals.read(rows,[])
    previous_equity=previous["total_equity"]
    previous_top=[dict(item) for item in previous["top_margin_accounts"]]
    for cycle in range(20):
        for login in range(1,1561):
            key=str(login); old=rows[key]
            floating=round(10 + (cycle%7)*.01 + (login%5)*.01,2)
            new=dict(old,Equity=1000+floating,**{"Free Margin":995+floating,"Live Floating PnL":floating,"Floating PnL":floating})
            totals.replace(key,old,new); rows[key]=new
        actual=totals.read(rows,[])
        for output,field in totals.FIELDS.items():
            assert actual[output] == round(math.fsum(value[field] for value in rows.values()),2)
    assert previous["total_equity"] == previous_equity
    assert previous["top_margin_accounts"] == previous_top
    assert actual["total_balance"] == round(math.fsum(value["Balance"] for value in rows.values()),2)
    # A real balance/margin change, closure and deletion must still propagate.
    old=rows["3"]; new=dict(old,Balance=1200.01,Margin=0,**{"Free Margin":1200.01})
    totals.replace("3",old,new); rows["3"]=new
    totals.replace("4",rows.pop("4"),None)
    actual=totals.read(rows,[])
    for output,field in totals.FIELDS.items():
        assert actual[output] == round(math.fsum(value[field] for value in rows.values()),2)
    assert actual["total_users"] == 1599
    assert previous["total_equity"] == previous_equity


def test_identical_nonfinite_value_cannot_bypass_finite_sum_validation():
    totals=NativeAccountTotals()
    original=row(1)
    totals.replace("1",None,original)
    bad=dict(original,Balance=math.inf)
    with pytest.raises(ValueError): totals.replace("1",bad,bad)
