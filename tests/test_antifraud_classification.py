"""Boundary and missing-data checks for Anti-Fraud classification."""

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

import api
from mt5_connection import MT5ConnectionManager


def scan(deals=(), positions=(), account=None, ticks=(), **settings):
    with ExitStack() as stack:
        stack.enter_context(patch.object(api, "_collect_user_scope", return_value=({1001: {"name": "Trader"}}, {1001: "live"}, [])))
        stack.enter_context(patch.object(api, "request_deals_by_group", return_value=(list(deals), None)))
        stack.enter_context(patch.object(api, "request_positions_by_group", return_value=(list(positions), None)))
        stack.enter_context(patch.object(api, "request_accounts_by_logins", return_value=({1001: account} if account is not None else {}, [])))
        stack.enter_context(patch.object(api, "request_tick_history", return_value=(list(ticks), None)))
        stack.enter_context(patch.object(api, "request_news", return_value=([], None)))
        return api.build_antifraud_snapshot(object(), ["live"], **settings)


def trade(ticket, opened, pnl=25, *, precise=True, hold=60):
    entry = {"Deal": ticket * 2, "PositionID": ticket, "Login": 1001, "Action": api.DEAL_BUY, "Entry": 0, "Time": opened, "Symbol": "EURUSD", "Volume": 10000, "Price": 1.1}
    if precise:
        entry["TimeMsc"] = opened * 1000
    exit_deal = {"Deal": ticket * 2 + 1, "PositionID": ticket, "Login": 1001, "Action": api.DEAL_SELL, "Entry": api.ENTRY_OUT, "Time": opened + hold, "Symbol": "EURUSD", "Volume": 10000, "Profit": pnl}
    return [entry, exit_deal]


def events(snapshot, rule):
    return [event for event in snapshot["events"] if event["rule"] == rule]


def test_latency_exact_boundary_and_missing_tick_coverage():
    opened = 1_800_000_000
    ticks = [{"datetime_msc": (opened - 3) * 1000, "bid": 1.0, "ask": 1.1}, {"datetime_msc": opened * 1000, "bid": 1.0, "ask": 1.1}]
    positive = scan(trade(1, opened, pnl=10, hold=300), ticks=ticks)
    assert len(events(positive, "latency_arbitrage")) == 1
    assert events(positive, "latency_arbitrage")[0]["data_quality"] == "VERIFIED"
    assert not events(scan(trade(1, opened, pnl=9.99), ticks=ticks), "latency_arbitrage")
    assert not events(scan(trade(1, opened), ticks=ticks[:1]), "latency_arbitrage")
    missing_precision = scan(trade(1, opened, precise=False), ticks=ticks)
    assert not events(missing_precision, "latency_arbitrage")
    assert missing_precision["unchecked_candidates"][0]["status"] == "UNCHECKED"
    offset_ticks = [ticks[0], {**ticks[1], "datetime_msc": opened * 1000 + 1}]
    assert not events(scan(trade(1, opened), ticks=offset_ticks), "latency_arbitrage")


def test_short_hold_is_behavior_until_rapid_frequency_is_present():
    start = 1_800_000_000
    three = sum((trade(i, start + i * 120) for i in range(1, 4)), [])
    short = scan(three)
    assert len(events(short, "short_hold_scalping")) == 1
    assert not events(short, "scalpers_hft")
    assert events(short, "short_hold_scalping")[0]["category"] == "behavioral_indicator"
    assert not events(scan(three[:4]), "short_hold_scalping")
    rapid = sum((trade(i, start + i * 2, hold=1) for i in range(1, 11)), [])
    assert len(events(scan(rapid), "scalpers_hft")) == 1


def test_exposure_requires_specification_price_and_capital():
    position = {"Position": 10, "Login": 1001, "Symbol": "EURUSD", "Action": 0, "Volume": 10000, "PriceCurrent": 1.0, "ContractSize": 100000, "RateProfit": 1.0}
    assert not events(scan(positions=[position], account={"Balance": 100001, "Equity": 100001}), "large_volume_order")
    event = events(scan(positions=[position], account={"Balance": 100000, "Equity": 100000}, large_order_exposure_ratio=1), "large_volume_order")
    assert len(event) == 1
    assert event[0]["triggering_values"]["exposure"] == 100000
    assert not events(scan(positions=[{key: value for key, value in position.items() if key != "ContractSize"}], account={"Equity": 1000}), "large_volume_order")
    assert not events(scan(positions=[{key: value for key, value in position.items() if key != "RateProfit"}], account={"Equity": 1000}), "large_volume_order")
    assert not events(scan(positions=[position]), "large_volume_order")


def test_funding_classification_and_correlation():
    opened = 1_800_000_000
    deposit = {"Deal": 99, "Login": 1001, "Action": api.DEAL_BALANCE, "Time": opened - 1, "Profit": 100}
    base = trade(1, opened, pnl=300)
    combined = scan([deposit, *base])
    funding = [event for event in combined["events"] if event["rule"] in {"low_deposit_high_profit", "lifetime_deposit_high_profit", "starting_capital_high_profit"}]
    assert len(funding) == 1
    assert funding[0]["data_quality"] == "PARTIAL"
    assert set(funding[0]["correlated_rules"]) == {"low_deposit_high_profit", "lifetime_deposit_high_profit"}
    for action in (api.DEAL_CREDIT, api.DEAL_CORRECTION, api.DEAL_BONUS):
        assert not events(scan([{**deposit, "Action": action}, *base]), "starting_capital_high_profit")
    assert not events(scan([{**deposit, "Comment": "internal transfer"}, *base]), "starting_capital_high_profit")


def test_deposit_alert_threshold_without_trades_and_exact_deal_evidence():
    now = int(datetime.now(timezone.utc).timestamp())
    base = {"Deal": 99, "Login": 1001, "Action": api.DEAL_BALANCE,
            "Time": now - 10, "Profit": 1000}
    result = scan([base, dict(base)], deposit_alert_threshold=1000)
    found = events(result, "deposit_alert")
    assert len(found) == 1
    assert found[0]["category"] == "risk_indicator"
    assert found[0]["data_quality"] == "PARTIAL"
    assert found[0]["triggering_values"]["deal_id"] == 99
    assert found[0]["thresholds"]["minimum_deposit_amount"] == 1000
    assert not events(scan([base], deposit_alert_threshold=1000.01), "deposit_alert")
    assert len(events(scan([base, {**base, "Deal": 100}], deposit_alert_threshold=1000), "deposit_alert")) == 2


def test_deposit_alert_rejects_non_deposit_and_incomplete_data():
    now = int(datetime.now(timezone.utc).timestamp())
    base = {"Deal": 99, "Login": 1001, "Action": api.DEAL_BALANCE,
            "Time": now - 10, "Profit": 1200}
    for candidate in (
        {**base, "Action": api.DEAL_CREDIT},
        {**base, "Action": api.DEAL_BONUS},
        {**base, "Profit": -1200},
        {**base, "Comment": "internal transfer"},
        {**base, "Comment": "deposit reversal"},
        {**base, "Deal": None},
        {**base, "Time": None},
        {**base, "Profit": None},
    ):
        assert not events(scan([candidate], deposit_alert_threshold=1000), "deposit_alert")


def test_concentration_requires_configured_completed_trade_count():
    start = 1_800_000_000
    two = [*trade(1, start, pnl=200), *trade(2, start + 100, pnl=25)]
    assert not events(scan(two), "single_trade_profit_concentration")
    three = [*two, *trade(3, start + 200, pnl=0)]
    assert len(events(scan(three), "single_trade_profit_concentration")) == 1
    assert not events(scan(three, concentration_min_trades=4), "single_trade_profit_concentration")
    incomplete = [*two, *trade(3, start + 200, pnl=100)]
    incomplete[-1].pop("Profit")
    assert not events(scan(incomplete), "single_trade_profit_concentration")


def test_agent_commission_zero_profit_does_not_fall_back():
    deals = [
        {"Deal": 1, "Login": 1001, "Action": api.DEAL_COMMISSION_AGENT_DAILY, "Profit": 0, "Commission": 50},
        {"Deal": 2, "Login": 1001, "Action": api.DEAL_COMMISSION_AGENT_MONTHLY, "Commission": 4},
    ]
    commission = events(scan(deals), "agent_commissions")
    assert len(commission) == 1
    assert commission[0]["value"] == 4
    assert events(scan(deals[:1]), "agent_commissions")[0]["value"] == 0


def test_sdk_extended_volume_and_instant_agent_action():
    assert api._position_volume_lots({"VolumeExt": 100000000}) == 1.0
    assert api._deal_lots({"VolumeExt": 25000000}) == 0.25
    assert api._deal_volume_units({"VolumeExt": 25000000}) == 2500.0
    assert api._deal_volume_units({"Volume": 0, "VolumeExt": 25000000}) == 2500.0
    cached_position = MT5ConnectionManager._normalize_position(None, {"Login": 1001, "Symbol": "EURUSD", "Volume": 10000, "VolumeExt": 25000000})
    assert cached_position["Volume"] == 0.25
    instant = {"Deal": 3, "Login": 1001, "Action": api.DEAL_COMMISSION_AGENT_INSTANT, "Profit": 7}
    commission = events(scan([instant]), "agent_commissions")
    assert len(commission) == 1
    assert commission[0]["value"] == 7


def test_hedge_is_risk_indicator_with_complete_evidence():
    positions = [{"Position": action + 1, "Login": 1001, "Symbol": "EURUSD", "Action": action, "Volume": 10000} for action in (0, 1)]
    hedge = events(scan(positions=positions), "hedge_accounts")
    assert len(hedge) == 1
    assert hedge[0]["category"] == "risk_indicator"
    assert hedge[0]["triggering_values"]["buy_positions"] == 1
    assert hedge[0]["triggering_values"]["sell_positions"] == 1
    assert hedge[0]["related_event_ids"] == []
