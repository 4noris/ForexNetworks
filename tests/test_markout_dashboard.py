import importlib.util
from pathlib import Path

try:
    from services.markout_dashboard import dashboard
except ModuleNotFoundError:
    spec = importlib.util.spec_from_file_location("markout_dashboard", Path(__file__).with_name("markout_dashboard.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dashboard = module.dashboard


def source(**overrides):
    return {"executions": [{"symbol": "EURUSD", "volume_lots": 1, "contract_size": 100000,
        "rate_profit": 1, "execution_price": 1, "profit": 0, "commission": -2,
        "swap": 0, "fee": 0, "time_ms": 1, "horizons": {"900": {"execution_to_mid_bps": 10}}, **overrides}],
        "summary": {"900": {}}}


def test_verified_dollar_notional_dpm_and_recorded_costs():
    report = dashboard(source(), "USD", {"EURUSD": 0})
    assert report["volume_usd"] == 100000
    assert report["dpm"] == 1000
    assert report["cumulative"][0]["net"] == -2
    assert report["internalised_pct"] is None
    assert report["total_attribution_usd"] is None
    assert report["residual_usd"] is None


def test_missing_units_conversion_or_unsupported_mode_never_fabricates_dollars():
    for currency, modes, overrides in [("EUR", {"EURUSD": 0}, {}),
        ("USD", {}, {}), ("USD", {"EURUSD": 1}, {}),
        ("USD", {"EURUSD": 0}, {"contract_size": None}),
        ("USD", {"EURUSD": 0}, {"rate_profit": None})]:
        report = dashboard(source(**overrides), currency, modes)
        assert report["volume_usd"] is None
        assert report["dpm"] is None


def test_missing_cost_stays_unavailable_and_empty_report_has_no_fake_zero_volume():
    report = dashboard(source(swap=None), "USD", {"EURUSD": 0})
    assert report["cumulative"][0]["swap"] is None
    assert report["cumulative"][0]["net"] is None
    empty = dashboard({"executions": [], "summary": {"900": {}}}, "USD", {})
    assert empty["volume_usd"] is None
    assert empty["dpm"] is None
    assert empty["trades"] == 0
