from uuid import uuid4
import pytest
from test_news_push import service, enable, event


def test_manual_send_uses_fresh_profile_when_local_user_is_missing(service):
    push, manager = service
    assert manager.call('UserGet', 7) is False
    result = push.send_test(7, 'RMS test', 'admin', uuid4())
    assert result['status'] == 'accepted'
    assert [name for name, _ in manager.calls].count('UserRequest') == 1
    assert [name for name, _ in manager.calls].count('NotificationsSend') == 1


@pytest.mark.parametrize('fresh,detail', [(False, 'configured server'), ({'Login': 8, 'MQID': 'source-device'}, 'different account')])
def test_failed_server_verification_cannot_fall_back_to_cached_account(service, fresh, detail):
    push, manager = service
    original = manager.call
    def call(method, *args):
        if method == 'UserRequest':
            return fresh
        if method == 'UserGet':
            return {'Login': args[0], 'MQID': 'cached-device'}
        return original(method, *args)
    manager.call = call
    result = push.send_test(7, 'RMS test', 'admin', uuid4())
    assert result['status'] == 'failed'
    assert detail in result['detail']
    assert not any(name == 'NotificationsSend' for name, _ in manager.calls)


def test_automatic_push_reads_recipient_and_currency_from_server(service):
    push, manager = service
    enable(push)
    assert push.queue([event(push)]) == 1
    assert push.process_one() is True
    methods = [name for name, _ in manager.calls]
    assert methods == ['UserRequest', 'GroupRequest', 'NotificationsSend']
    assert push.status()['counts'] == {'accepted': 1}


def test_bootstrap_database_isolated_from_deployment_dotenv():
    import os
    from pathlib import Path
    import api
    assert Path(api.DB_PATH).resolve() == Path(os.environ['RMS_TEST_DB_PATH']).resolve()
    assert Path(api.DB_PATH).resolve() != Path(api.__file__).with_name('users.db').resolve()
