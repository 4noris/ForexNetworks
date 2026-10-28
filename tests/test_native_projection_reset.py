from mt5_connection import MT5ConnectionManager
from services.native_bridge import NativeProjection
from services.native_money import portfolio_summary
from test_native_bridge import snapshot


def test_replacement_snapshot_clears_old_financial_aggregates_and_clock():
    manager = MT5ConnectionManager("fixture", 1, "")
    state = NativeProjection()
    first = snapshot()
    first["server_time_msc"] = 123000
    state.snapshot(first)
    assert state.server_time_msc == 123000
    state.publish(manager)
    old_totals = manager.cache["native_money_totals"]
    assert portfolio_summary(old_totals, {"real"}, {"real": "USD"})["equity"] == 127
    state.coverage_pending_since = -100
    state.snapshot(snapshot())
    assert state.coverage_pending_since is None
    assert state.server_time_msc == 0
    state.publish(manager)
    assert portfolio_summary(manager.cache["native_money_totals"], {"real"}, {"real": "USD"})["equity"] == 127
    assert portfolio_summary(old_totals, {"real"}, {"real": "USD"})["equity"] == 127
