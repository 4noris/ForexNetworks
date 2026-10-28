import importlib.util
import math
from pathlib import Path
import threading
import time

import pytest

try:
    from services.native_bridge import NativeProjection, ResyncRequired, current_publish_boundary
except ModuleNotFoundError:
    spec = importlib.util.spec_from_file_location("native_bridge", Path(__file__).with_name("native_bridge.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    NativeProjection, ResyncRequired = module.NativeProjection, module.ResyncRequired
    current_publish_boundary = module.current_publish_boundary


def test_large_account_batches_are_drained_before_publishing():
    now = 10000
    # An update partway through a large response must not cause a full rebuild.
    assert not current_publish_boundary({"kind": "accounts", "op": "upsert", "received_ms": now}, {"accounts"}, now)
    # Completed but delayed batches must be consumed until the reader catches up.
    end = {"kind": "accounts", "op": "cycle_end", "received_ms": now - 5000}
    assert not current_publish_boundary(end, {"accounts", "positions"}, now)
    end["received_ms"] = now - 100
    assert current_publish_boundary(end, {"accounts", "positions"}, now)


def test_position_only_and_quote_only_sources_still_publish():
    now = 10000
    assert current_publish_boundary({"kind": "positions", "op": "replace", "data": [], "received_ms": now}, {"positions"}, now)
    assert not current_publish_boundary({"kind": "positions", "op": "replace", "data": [{"Ticket": "1"}], "received_ms": now}, {"positions"}, now)
    assert current_publish_boundary({"kind": "ticks", "op": "upsert", "received_ms": now}, {"ticks"}, now)
    assert not current_publish_boundary({"kind": "ticks", "op": "upsert", "received_ms": now + 5000}, {"ticks"}, now)


def snapshot():
    stamp = int(time.time() * 1000)
    return {"epoch": "real-source-epoch", "sequence": 10, "complete": True, "connected": True,
        "account_ms": stamp, "position_ms": stamp, "records": {
            "users": {"7": {"Login": 7, "Name": "Source user", "Group": "real"}},
            "accounts": {"7": {"Login": 7, "Balance": 100, "Credit": 3, "Equity": 127,
                "Margin": 10, "Free Margin": 117, "Margin Level %": 1270, "Floating PnL": 10, "Swap": 0}},
            "positions": {"1": {"Login": 7, "Ticket": "1", "Symbol": "EURUSD", "Profit": 10, "Swap": 0, "Volume": 0.05}},
            "groups": {"real": {"Group": "real", "Currency": "USD"}},
            "ticks": {"EURUSD": {"Bid": 1.1, "Ask": 1.2, "TimeMsc": 1700000000000}},
        }}


class Manager:
    def __init__(self):
        self.cache = {}
        self.cache_lock = threading.Lock()
    def _build_metrics(self, users, positions):
        return {"accounts": len(users), "positions": len(positions)}
    def _merge_live_position_pnl(self, users, by_login, positions):
        for user in users:
            user["Live Floating PnL"] = sum(row["Profit"] + row["Swap"] for row in positions if row["Login"] == user["Login"])


def test_native_projection_preserves_reported_equity_and_original_quote_time():
    state = NativeProjection()
    state.snapshot(snapshot())
    manager = Manager()
    state.publish(manager)
    assert manager.cache["users_by_login"][7]["Equity"] == 127  # Never reconstructed from other fields.
    assert manager.cache["positions_by_login"][7][0]["Volume"] == 0.05
    assert manager.cache["tick_cache"]["EURUSD"]["tick_time"].startswith("2023-")
    assert manager.cache["refresh_phase"] == "cpp_native_live"


def test_incomplete_or_invalid_accounts_never_become_fake_zero_values():
    for field, value in [("Equity", None), ("Margin", float("nan"))]:
        source = snapshot()
        source["records"]["accounts"]["7"][field] = value
        with pytest.raises(ResyncRequired):
            NativeProjection().snapshot(source)
    source = snapshot()
    source["records"]["accounts"] = {}
    with pytest.raises(ResyncRequired):
        NativeProjection().snapshot(source)


def test_sequence_gap_and_disconnect_force_full_resynchronization():
    state = NativeProjection()
    state.snapshot(snapshot())
    with pytest.raises(ResyncRequired):
        state.apply({"kind": "positions", "op": "delete", "id": "1", "sequence": 12, "epoch": state.epoch})
    with pytest.raises(ResyncRequired):
        state.apply({"kind": "status", "op": "status", "data": {"connected": False}, "sequence": 11, "epoch": state.epoch})


def test_closed_position_deletion_uses_exact_native_ticket():
    state = NativeProjection()
    state.snapshot(snapshot())
    state.apply({"kind": "positions", "op": "delete", "id": "1", "sequence": 11, "epoch": state.epoch})
    manager = Manager()
    state.publish(manager)
    assert manager.cache["positions"] == []
    assert manager.cache["users_by_login"][7]["Equity"] == 127


def test_incremental_publish_preserves_previous_account_generation():
    state = NativeProjection()
    state.snapshot(snapshot())
    manager = Manager()
    state.publish(manager)
    previous = manager.cache["users_by_login"][7]
    account = dict(state.records["accounts"]["7"], Equity=130)
    state.apply({"kind": "accounts", "op": "upsert", "id": "7", "data": account,
                 "sequence": 11, "epoch": state.epoch})
    state.apply({"kind": "positions", "op": "delete", "id": "1", "sequence": 12, "epoch": state.epoch})
    state.publish(manager)
    assert previous["Equity"] == 127
    assert previous["Live Floating PnL"] == 10
    assert manager.cache["users_by_login"][7]["Equity"] == 130
    assert manager.cache["users_by_login"][7]["Live Floating PnL"] == 0


def test_invalid_incremental_account_never_replaces_verified_metrics():
    state = NativeProjection()
    state.snapshot(snapshot())
    invalid = dict(state.records["accounts"]["7"], Equity=None)
    with pytest.raises(ResyncRequired):
        state.apply({"kind": "accounts", "op": "upsert", "id": "7", "data": invalid,
                     "sequence": 11, "epoch": state.epoch})
    assert state.records["accounts"]["7"]["Equity"] == 127
