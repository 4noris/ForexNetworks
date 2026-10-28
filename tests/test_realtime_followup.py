from types import SimpleNamespace
from fastapi import Response
import api
import mt5_connection
from test_dealer_command_center import FakeManager


def test_net_lots_offsets_buys_and_sells(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'DB_PATH', tmp_path / 'net.db')
    api.init_db()
    user = {'Login': 7, 'Group': 'live', 'Currency': 'USD'}
    positions = [{'Login': 7, 'Symbol': 'EURUSD', 'Ticket': i, 'Action': side, 'Volume': lots} for i, side, lots in [(1, 0, 2.5), (2, 1, 1.75), (3, 1, 1.0)]]
    monkeypatch.setattr(api, 'get_configured_manager', lambda: FakeManager([user], positions))
    monkeypatch.setattr(api.time_module, 'time', lambda: 1800000001)
    result = api.dealer_command_center(api.GroupScopedRequest(selected_groups=['live']))
    assert result['kpis']['gross_lots'] == 5.25
    assert result['kpis']['net_lots'] == -0.25


def test_book_live_metrics_do_not_invent_missing_history(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'DB_PATH', tmp_path / 'book-live.db')
    api.init_db()
    monkeypatch.setattr(api, 'get_book_group_settings', lambda: {'b_book': ['live']})
    monkeypatch.setattr(api, '_start_book_history_backfill', lambda *_: None)
    monkeypatch.setattr(api.time_module, 'time', lambda: 1800000001)
    view = {'last_update': 1800000000, 'errors': [], 'users': [{'Login': 7, 'Currency': 'USD', 'Balance': 100, 'Equity': 127}], 'positions': [{'Profit': 12, 'Swap': -1}]}
    monkeypatch.setattr(mt5_connection, 'get_manager', lambda: SimpleNamespace(is_connected=True, live_risk_cache_view=lambda **_: view))
    result = api.get_book_pnl(Response(), 'b_book', api.date(2026, 8, 1), api.date(2026, 8, 2))
    assert result['live']['equity'] == 127
    assert result['live']['floating'] == 11
    assert result['daily'] == []
    assert result['coverage']['complete'] is False
    view['users'][0]['Currency'] = 'EUR'
    assert api.get_book_pnl(Response(), 'b_book', api.date(2026, 8, 1), api.date(2026, 8, 2))['live'] is None
    view['users'][0]['Currency'] = 'USD'
    view['last_update'] -= 20
    assert api.get_book_pnl(Response(), 'b_book', api.date(2026, 8, 1), api.date(2026, 8, 2))['live'] is None


def test_native_antifraud_expiry_refreshes_without_reading_sqlite(monkeypatch):
    request = api.AntiFraudRequest(selected_groups=['live'])
    key = api._antifraud_cache_key(request)
    started = []
    monkeypatch.setenv('RMS_NATIVE_URL', 'http://127.0.0.1:8000')
    monkeypatch.setattr(api, 'ANTIFRAUD_CACHE_SECONDS', 60)
    monkeypatch.setattr(api, '_ANTIFRAUD_CACHE', {key: (api.time_module.monotonic() - 6, {'generated_at': '2026-10-27T20:00:00+00:00'})})
    monkeypatch.setattr(api, '_ANTIFRAUD_REFRESHING', set())
    monkeypatch.setattr(api, '_conn', lambda: (_ for _ in ()).throw(AssertionError('Live stale read must not wait on SQLite')))
    monkeypatch.setattr(api.threading, 'Thread', lambda **_: SimpleNamespace(start=lambda: started.append(True)))
    result = api.get_antifraud_snapshot(request)
    assert result['cache']['refreshing'] is True
    assert result['cache']['ttl_seconds'] == 5
    assert len(started) == 1
    api.get_antifraud_snapshot(request)
    assert len(started) == 1
