import math
import random

from mt5_connection import MT5ConnectionManager
from services.native_bridge import NativeProjection
from services.native_totals import ExactFloatSum, NativeAccountTotals
from test_native_bridge import snapshot
from test_native_changes import change_frame


def make_row(rng, login):
    return {"Login":login,"Name":str(login),"Group":"test-only","Enabled":login%3!=0,
            **{field:round(rng.uniform(-100, 100000),2) for field in
               ("Balance","Equity","Margin","Free Margin","Live Floating PnL","Floating PnL","Margin Level %")}}


def test_incremental_totals_match_full_book_through_changes_closures_and_deletions():
    rng = random.Random(11)
    totals = NativeAccountTotals()
    rows = {}
    for login in range(1,2001):
        key = str(login)
        rows[key] = make_row(rng,login)
        totals.replace(key,None,rows[key])
    for cycle in range(20):
        for login in rng.sample(range(1,2001),100):
            key = str(login)
            old = rows.get(key)
            if cycle%5 == 0:
                rows.pop(key,None)
                totals.replace(key,old,None)
            else:
                rows[key] = make_row(rng,login)
                totals.replace(key,old,rows[key])
        assert totals.read(rows,[]) == MT5ConnectionManager._build_native_metrics(list(rows.values()),[])


def test_replacement_sum_preserves_small_values_and_does_not_drift():
    total = ExactFloatSum()
    values = [1e16,1.01,-1e16,100.1234567]
    for value in values:
        total.add(value)
    assert total.value() == math.fsum(values)
    for _ in range(10000):
        total.add(-100.1234567)
        total.add(100.1234567)
    assert total.value() == math.fsum(values)


def test_margin_heap_stays_bounded_and_equal_margins_keep_account_order():
    rng = random.Random(1)
    rows = {str(login):make_row(rng,login) for login in range(1,11)}
    totals = NativeAccountTotals()
    for key,row in rows.items():
        row["Margin"] = 0
        totals.replace(key,None,row)
    for n in range(5000):
        old = rows["1"]
        rows["1"] = dict(old,Margin=n%2)
        totals.replace("1",old,rows["1"])
        actual = totals.read(rows,[])
        assert len(totals.heap)<=1024
    assert actual == MT5ConnectionManager._build_native_metrics(list(rows.values()),[])


def test_incremental_projection_preserves_previous_generation_and_rebuilds_membership():
    manager = MT5ConnectionManager("test-only",1,"")
    state = NativeProjection()
    state.snapshot(snapshot())
    first = state.capture()
    first.publish(manager)
    old_users = manager.cache["users"]
    old_metrics = manager.cache["metrics"]
    update = change_frame(state)
    state.apply_changes(update)
    second = state.capture(first)
    second.publish(manager)
    assert old_users[0]["Equity"] == 127
    assert old_metrics["total_equity"] == 127
    assert manager.cache["users_by_login"][7]["Equity"] == 321
    assert manager.cache["metrics"]["total_equity"] == 321
    delete = change_frame(state)
    delete["updates"] = {"users":{"7":None},"accounts":{"7":None}}
    state.apply_changes(delete)
    third = state.capture(second)
    third.publish(manager)
    assert manager.cache["users"] == []
    assert manager.cache["metrics"]["total_equity"] == 0
    assert third.merged_users == {}


def test_missing_account_keeps_preceding_verified_generation():
    manager = MT5ConnectionManager("test-only",1,"")
    state = NativeProjection()
    state.snapshot(snapshot())
    first = state.capture()
    first.publish(manager)
    original = manager.cache["users"]
    update = change_frame(state)
    update["updates"] = {"accounts":{"7":None}}
    state.apply_changes(update)
    state.capture(first).publish(manager)
    assert manager.cache["users"] is original
