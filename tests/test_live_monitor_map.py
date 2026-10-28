import pytest
import api


def test_live_monitor_net_lots_offsets_buys_and_sells(monkeypatch):
    monkeypatch.setattr(api, 'enrich_ip_addresses_nonblocking', lambda _: {})
    users = [{'Login': 7, 'Group': 'real', 'Currency': 'USD', 'Balance': 100, 'Equity': 100}]
    positions = [{'Ticket': i, 'Login': 7, 'Symbol': 'EURUSD', 'Action': side, 'Volume': volume} for i, side, volume in [(1, 0, 1.5), (2, 1, 1), (3, 1, .75)]]
    result = api.build_live_risk_snapshot_from_cache(users, positions, ['real'])
    assert result['summary']['net_lots'] == -.25
    assert result['summary']['gross_lots'] == 3.25
    assert result['summary']['buy_lots'] == 1.5
    assert result['summary']['sell_lots'] == 1.75


@pytest.mark.parametrize('latitude,longitude', [(None, None), ('', ''), ('NaN', 20), (91, 0), (0, -181), (True, False)])
def test_missing_or_invalid_geolocation_is_not_faked_as_zero(latitude, longitude):
    intel = api._normalize_ip_intel('198.51.100.1', {'status': 'success', 'lat': latitude, 'lon': longitude})
    assert intel['latitude'] is None or intel['longitude'] is None


def test_real_zero_coordinates_are_preserved():
    intel = api._normalize_ip_intel('198.51.100.1', {'status': 'success', 'lat': 0, 'lon': 0})
    assert intel['latitude'] == 0
    assert intel['longitude'] == 0
