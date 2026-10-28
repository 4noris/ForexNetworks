import copy

import pytest

from services.native_bridge import NativeProjection,ResyncRequired
from test_native_bridge import Manager,snapshot
from test_native_changes import change_frame


def test_field_patch_preserves_trade_identity_and_updates_live_profit():
    state=NativeProjection()
    state.snapshot(snapshot())
    before=copy.deepcopy(state.records["positions"]["1"])
    frame=change_frame(state)
    frame["updates"]={}
    frame["patches"]={"positions":{"1":{"Profit":-25.51}}}
    assert "positions" in state.apply_changes(frame)
    assert state.records["positions"]["1"]==dict(before,Profit=-25.51)
    manager=Manager()
    state.publish(manager)
    assert manager.cache["positions"][0]["Profit"]==-25.51
    assert manager.cache["users_by_login"][7]["Live Floating PnL"]==round(-25.51+before.get("Swap",0),2)


@pytest.mark.parametrize("corrupt",["missing","unknown_field","duplicate","invalid_kind","invalid_rows"])
def test_invalid_field_patch_cannot_partially_apply_batch(corrupt):
    state=NativeProjection()
    state.snapshot(snapshot())
    frame=change_frame(state)
    frame["updates"]={"accounts":{"7":dict(state.records["accounts"]["7"],Equity=999)}}
    frame["patches"]={"positions":{"1":{"Profit":15}}}
    if corrupt=="missing":frame["patches"]["positions"]={"999":{"Profit":15}}
    elif corrupt=="unknown_field":frame["patches"]["positions"]["1"]["Unknown"]=1
    elif corrupt=="duplicate":frame["updates"]["positions"]={"1":state.records["positions"]["1"]}
    elif corrupt=="invalid_kind":frame["patches"]={"accounts":{"7":{"Equity":1}}}
    else:frame["patches"]["positions"]=[]
    before=copy.deepcopy(state.records)
    with pytest.raises(ResyncRequired):state.apply_changes(frame)
    assert state.records==before
    assert state.sequence==10
