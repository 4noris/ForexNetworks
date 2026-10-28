from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
import api
from services.news_push import NewsPushService, utc
from services.websocket_reads import read_target
from test_news_push import service


def test_manual_test_works_with_automatic_alerts_disabled_and_preserves_history(service):
    push, manager = service
    key = str(uuid4())
    result = push.send_test(7, 'RMS test {sent as typed}', 'admin', key)
    assert result['status'] == 'accepted'
    assert result['login'] == '7'
    assert push.settings()['enabled'] is False
    assert push.status()['counts'] == {}
    assert push.status()['last_test']['request_id'] == key
    with api._conn() as con:
        assert con.execute('SELECT COUNT(*) FROM news_trader_incidents').fetchone()[0] == 0
    calls = [args for method, args in manager.calls if method == 'NotificationsSend']
    assert calls == [([7], 'RMS test {sent as typed}')]


def test_retries_and_restart_do_not_repeat_a_test_push(service):
    push, manager = service
    key = uuid4()
    first = push.send_test(7, 'Test', 'admin', key)
    assert push.send_test(7, 'Test', 'admin', key) == first
    restored = NewsPushService(api._conn, lambda: manager)
    assert restored.send_test(7, 'Test', 'admin', key) == first
    with pytest.raises(ValueError):
        restored.send_test(8, 'Test', 'admin', key)
    assert len([call for call in manager.calls if call[0] == 'NotificationsSend']) == 1


@pytest.mark.parametrize('result,expected', [(False, 'failed'), (0, 'accepted'), (None, 'unknown'), (TimeoutError(), 'unknown')])
def test_manual_failed_or_ambiguous_sends_are_not_retried(service, result, expected):
    push, manager = service
    manager.result = result
    key = str(uuid4())
    assert push.send_test(7, 'Test', 'admin', key)['status'] == expected
    assert push.send_test(7, 'Test', 'admin', key)['status'] == expected
    assert len([call for call in manager.calls if call[0] == 'NotificationsSend']) == 1


def test_missing_metaquotes_id_prevents_a_manual_send(service):
    push, manager = service
    manager.mqid = ''
    result = push.send_test(7, 'Test', 'admin', uuid4())
    assert result['status'] == 'skipped'
    assert 'MetaQuotes ID' in result['detail']
    assert not any(method == 'NotificationsSend' for method, _ in manager.calls)


def test_recovery_does_not_invalidate_a_test_started_by_this_process(service):
    push, manager = service
    key = str(uuid4())
    with api._conn() as con:
        con.execute("INSERT INTO news_push_tests(request_id,login,message,actor,status,created_at) VALUES(?,7,'Test','admin','sending',?)", (key, utc()))
    push.recover()
    assert push.status()['last_test']['status'] == 'sending'
    restored = NewsPushService(api._conn, lambda: manager)
    restored.started_at = '9999-01-01T00:00:00+00:00'
    restored.recover()
    assert restored.send_test(7, 'Test', 'admin', key)['status'] == 'unknown'
    assert manager.calls == []


def test_admin_test_route_preserves_large_login_and_rejects_non_admin(service, monkeypatch):
    push, manager = service
    monkeypatch.setattr(api, 'NEWS_PUSH', push)
    monkeypatch.setenv('MT5_MANAGER_AUTOCONNECT', 'false')
    user = {'id': 1, 'username': 'fixture-admin', 'is_admin': True, 'must_change_pw': False}
    monkeypatch.setattr(api, '_verify_session_token', lambda token: user if token == 'fixture-token' else None)
    body = {'login': '9223372036854775806', 'message': 'RMS test', 'request_id': str(uuid4())}
    with TestClient(api.api) as client:
        assert client.post('/api/notifications/news-push/test', json=body).status_code == 401
        client.cookies.set(api.SESSION_COOKIE_NAME, 'fixture-token')
        client.headers['Authorization'] = 'Bearer fixture-token'
        result = client.post('/api/notifications/news-push/test', json=body)
        assert result.status_code == 200
        assert result.json()['status'] == 'accepted'
        assert result.json()['login'] == body['login']
        assert client.post('/api/notifications/news-push/test', json=body).json()['status'] == 'accepted'
        for login in [False, 7.5, '7.5', '-1', '0']:
            assert client.post('/api/notifications/news-push/test', json={**body, 'login': login}).status_code == 422
        user['is_admin'] = False
        assert client.post('/api/notifications/news-push/test', json=body).status_code == 403
    assert len([call for call in manager.calls if call[0] == 'NotificationsSend']) == 1
    assert push.settings()['enabled'] is False
    with pytest.raises(ValueError):
        read_target('POST', '/api/notifications/news-push/test')
