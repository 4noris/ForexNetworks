import api
import pytest
from services.native_bridge import NativeProjection, ResyncRequired
from test_native_bridge import Manager, snapshot


def news(position=77, release=1793100000, quality='VERIFIED'):
    return {'rule': 'news_trading', 'detector': 'news_trading', 'login': 7, 'position_id': position,
            'symbol': 'EURUSD', 'group': 'live', 'data_quality': quality,
            'triggering_values': {'calendar_event_ts': release}, 'thresholds': {'window_minutes': 5},
            'evidence': 'Source trade matched a calendar release'}


def test_news_counts_distinct_incidents_once_and_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'DB_PATH', tmp_path / 'news.db')
    api.init_db()
    event = news()
    assert len(api.persist_antifraud_events([event])) == 1
    assert event['news_flag'] == 'News trader 1x'
    assert api.persist_antifraud_events([{**news(), 'thresholds': {'window_minutes': 10}}]) == []
    second, third = news(78), news(79)
    api.persist_antifraud_events([second, third, news()])
    assert third['news_flag'] == 'News trader 3x'
    api.init_db()
    assert api._record_news_incidents([news()]) == {7: 3}
    with api._conn() as con:
        assert con.execute('SELECT COUNT(*) FROM news_trader_incidents').fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM risk_events WHERE detector='news_trading'").fetchone()[0] == 3


def test_unverified_or_unidentified_news_cannot_increment_counter(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'DB_PATH', tmp_path / 'news-partial.db')
    api.init_db()
    assert api._record_news_incidents([news(quality='PARTIAL'), news(position=0), news(release=0)]) == {}


def test_user_profile_handoff_keeps_previous_verified_generation(monkeypatch):
    state = NativeProjection()
    state.snapshot(snapshot())
    manager = Manager()
    state.publish(manager)
    before = manager.cache['users']
    state.records['users']['8'] = {'Login': 8, 'Group': 'real'}
    state.publish(manager)
    assert manager.cache['users'] is before
    state.records['accounts']['8'] = dict(state.records['accounts']['7'], Login=8)
    state.publish(manager)
    assert {row['Login'] for row in manager.cache['users']} == {7, 8}
    assert manager.cache['users_by_login'][8]['Equity'] == 127
    state.records['users']['9'] = {'Login': 9}
    state.coverage_pending_since = -100
    with pytest.raises(ResyncRequired):
        state.publish(manager)
