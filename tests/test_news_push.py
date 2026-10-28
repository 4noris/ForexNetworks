from types import SimpleNamespace
import pytest
from fastapi import HTTPException
import api
from services.news_push import NewsPushService, DEFAULT_MESSAGE


class Manager:
    is_connected = True
    def __init__(self, result=True, mqid="source-mqid", currency="USD"):
        self.calls = []
        self.result, self.mqid, self.currency = result, mqid, currency

    def call(self, method, *args):
        self.calls.append((method, args))
        if method == "UserGet":
            return False
        if method == "UserRequest":
            return {"Login": args[0], "MQID": self.mqid, "Group": "real"}
        if method == "GroupGet":
            return False
        if method == "GroupRequest":
            return {"Currency": self.currency}
        if method == "NotificationsSend":
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        raise AssertionError(method)


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "push.db")
    api.init_db()
    manager = Manager()
    return NewsPushService(api._conn, lambda: manager), manager


def enable(service, **changes):
    return service.save(dict(enabled=True, minimum_profit=100, currency="USD", message=DEFAULT_MESSAGE) | changes, "test-admin")


def event(service, **changes):
    data = {"rule": "news_trading", "login": 7, "position_id": 77, "symbol": "EURUSD", "data_quality": "VERIFIED",
            "account_currency": "USD", "news_incident_count": 3,
            "triggering_values": {"calendar_event_ts": 1793100000, "realized_pnl": 250.25,
                                  "trade_open_ts": service.settings()["enabled_since"] + 1}}
    data.update(changes)
    return data


def test_disabled_by_default_never_calls_mt5(service):
    push, manager = service
    assert push.settings()["enabled"] is False
    assert push.queue([event(push)]) == 0
    assert push.process_one() is False
    assert manager.calls == []


def test_one_verified_profitable_incident_sends_once_across_restart(service):
    push, manager = service
    enable(push)
    data = event(push)
    assert push.queue([data, data]) == 1
    assert push.process_one() is True
    assert push.process_one() is False
    restored = NewsPushService(api._conn, lambda: manager)
    assert restored.settings()["enabled"] is True
    assert restored.queue([data]) == 0
    calls = [args for method, args in manager.calls if method == "NotificationsSend"]
    assert len(calls) == 1
    assert calls[0][0] == [7]
    assert "250.25 USD" in calls[0][1]
    assert "matches: 3" in calls[0][1]
    assert push.status()["counts"] == {"accepted": 1}


def test_only_new_verified_high_profit_matching_currency_is_queued(service):
    push, manager = service
    enable(push)
    data = event(push)
    assert push.queue([
        event(push, data_quality="PARTIAL"), event(push, account_currency="EUR"),
        event(push, triggering_values={**data["triggering_values"], "realized_pnl": 99}),
        event(push, triggering_values={**data["triggering_values"], "realized_pnl": float("nan")}),
        event(push, triggering_values={**data["triggering_values"], "trade_open_ts": push.settings()["enabled_since"] - 1}),
        event(push, triggering_values={**data["triggering_values"], "trade_open_ts": float("nan")}),
    ]) == 0
    assert manager.calls == []


@pytest.mark.parametrize("result, expected", [(True, "accepted"), (0, "accepted"), (False, "failed"), (4, "failed"), (None, "unknown"), (TimeoutError(), "unknown")])
def test_result_codes_and_unknown_sends_are_not_retried(service, result, expected):
    push, manager = service
    manager.result = result
    enable(push)
    push.queue([event(push)])
    push.process_one()
    push.process_one()
    assert push.status()["counts"] == {expected: 1}
    assert len([item for item in manager.calls if item[0] == "NotificationsSend"]) == 1


@pytest.mark.parametrize("mqid,currency", [("", "USD"), ("source-mqid", "EUR")])
def test_missing_device_or_currency_mismatch_prevents_send(service, mqid, currency):
    push, manager = service
    manager.mqid, manager.currency = mqid, currency
    enable(push)
    push.queue([event(push)])
    push.process_one()
    assert push.status()["counts"] == {"skipped": 1}
    assert not any(method == "NotificationsSend" for method, _ in manager.calls)


def test_disable_cancels_queue_and_stops_send(service):
    push, manager = service
    enable(push)
    push.queue([event(push)])
    config = push.settings()
    push.save({**config, "enabled": False}, "test-admin")
    assert push.process_one() is False
    assert manager.calls == []
    assert push.status()["counts"] == {"cancelled": 1}


def test_crashed_inflight_send_is_unknown_after_recovery(service):
    push, manager = service
    enable(push)
    push.queue([event(push)])
    with api._conn() as con:
        con.execute("UPDATE news_push_outbox SET status='sending'")
    push.recover()
    assert push.process_one() is False
    assert push.status()["counts"] == {"unknown": 1}
    assert manager.calls == []


@pytest.mark.parametrize("message", ["{unknown}", "{login.__class__}", "{profit:.2f}", "{login!r}", "{", " " , "x" * 1025])
def test_invalid_message_cannot_enable_delivery(service, message):
    push, _ = service
    with pytest.raises(ValueError):
        push.save(dict(enabled=True, minimum_profit=100, currency="USD", message=message), "test-admin")
    assert push.settings()["enabled"] is False


def test_rendered_message_limit_is_checked_before_send(service):
    push, manager = service
    enable(push, message="x" * 1017 + "{login}")
    push.queue([event(push, login=1234567890123456)])
    push.process_one()
    assert push.status()["counts"] == {"failed": 1}
    assert not any(method == "NotificationsSend" for method, _ in manager.calls)


def test_non_admin_cannot_read_or_enable_client_pushes(service, monkeypatch):
    push, _ = service
    monkeypatch.setattr(api, "NEWS_PUSH", push)
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "reader", "is_admin": False}))
    with pytest.raises(HTTPException) as read:
        api.news_push_status(request)
    assert read.value.status_code == 403
    with pytest.raises(HTTPException) as write:
        api.update_news_push(api.NewsPushSettings(enabled=True, message=DEFAULT_MESSAGE), request)
    assert write.value.status_code == 403
    assert push.settings()["enabled"] is False

def test_admin_control_http_roundtrip_does_not_send_notifications(service, monkeypatch):
    from fastapi.testclient import TestClient
    push, manager = service
    monkeypatch.setattr(api, 'NEWS_PUSH', push)
    monkeypatch.setenv('MT5_MANAGER_AUTOCONNECT', 'false')
    user = {'id': 123, 'username': 'test-admin', 'is_admin': True, 'must_change_pw': False}
    monkeypatch.setattr(api, '_verify_session_token', lambda _token: user)
    with TestClient(api.api) as client:
        client.cookies.set(api.SESSION_COOKIE_NAME, 'fixture-session')
        assert client.get('/api/notifications/news-push').json()['settings']['enabled'] is False
        body = dict(enabled=True, minimum_profit=500, currency='USD', message=DEFAULT_MESSAGE)
        response = client.put('/api/notifications/news-push', json=body)
        assert response.status_code == 200
        assert response.json()['settings']['enabled'] is True
        assert client.get('/api/notifications/news-push').json()['settings']['minimum_profit'] == 500
        user['is_admin'] = False
        assert client.put('/api/notifications/news-push', json=body).status_code == 403
        assert client.get('/api/notifications/news-push').status_code == 403
        user['is_admin'] = True
        assert client.put('/api/notifications/news-push', json={**body, 'enabled': False}).status_code == 200
    assert manager.calls == []
