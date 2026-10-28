import copy
import time

import pytest

from services.native_bridge import NativeProjection, ResyncRequired
from test_native_bridge import Manager, snapshot


def change_frame(state):
    stamp = int(time.time() * 1000)
    return {"op": "changes", "epoch": state.epoch, "base": state.sequence,
            "sequence": state.sequence + 100000, "connected": True, "complete": True,
            "account_ms": stamp, "position_ms": stamp, "server_time_msc": stamp,
            "updates": {"accounts": {"7": dict(state.records["accounts"]["7"], Equity=321)},
                        "positions": {"1": None}}}


def test_state_replication_accepts_coalesced_version_span_and_closed_positions():
    state = NativeProjection()
    state.snapshot(snapshot())
    frame = change_frame(state)
    changed = state.apply_changes(frame)
    assert changed >= {"accounts", "positions"}
    assert state.sequence == frame["sequence"]
    manager = Manager()
    state.publish(manager)
    assert manager.cache["users_by_login"][7]["Equity"] == 321
    assert manager.cache["positions"] == []


@pytest.mark.parametrize("corrupt", ["base", "epoch", "financial", "timestamp", "disconnected"])
def test_invalid_state_batch_does_not_overwrite_previously_verified_metrics(corrupt):
    state = NativeProjection()
    state.snapshot(snapshot())
    frame = change_frame(state)
    if corrupt == "base": frame["base"] -= 1
    elif corrupt == "epoch": frame["epoch"] = "different-source"
    elif corrupt == "financial": frame["updates"]["accounts"]["7"]["Equity"] = None
    elif corrupt == "timestamp": frame["account_ms"] = None
    else: frame["connected"] = False
    records = copy.deepcopy(state.records)
    with pytest.raises(ResyncRequired):
        state.apply_changes(frame)
    assert state.records == records
    assert state.sequence == 10


def test_latest_quote_batches_do_not_require_account_republication():
    state = NativeProjection()
    state.snapshot(snapshot())
    frame = change_frame(state)
    frame.update(account_ms=state.account_ms, position_ms=state.position_ms, server_time_msc=0)
    frame["updates"] = {"ticks": {"EURUSD": {"Symbol": "EURUSD", "Bid": 1.3, "Ask": 1.4, "TimeMsc": int(time.time()*1000)}}}
    assert state.apply_changes(frame) == {"ticks"}
