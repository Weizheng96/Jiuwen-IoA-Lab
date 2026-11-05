"""Password failures, successful multi-account onboarding and original login recovery."""
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import pytest
from a2c.errors import A2CError

from ioalab.config import Settings
from ioalab.identity import auth as authentication
from ioalab.identity.auth import Auth, RateLimit
from ioalab.migrations import migrate
from ioalab.storage import DB
from _support import PASSWORD, username


@pytest.fixture
def auth(tmp_path):
    database = DB(tmp_path / 'auth.sqlite')
    migrate(database)
    result = Auth(database, Settings(mode='trial', registration='open', data=tmp_path,
                                     admin_password=PASSWORD,auth_max_failures=5,auth_window_seconds=3600,local_unlimited=False))
    result.register('known-user', PASSWORD, kind='virtual')
    try:
        yield result
    finally:
        database.close()


def test_trial_multi_account_login_and_original_credit_recovery(platform, client, monkeypatch):
    # Eight ordinary accounts share the same real HTTP source, as an installation
    # does. External users and internal labels use exactly the same password path.
    accounts = [platform.accounts.create_account(username(), PASSWORD, kind=kind)
                for kind in ('external', 'isp') * 4]
    monkeypatch.setattr(platform.s, 'mode', 'trial')
    monkeypatch.setattr(platform.auth, 'rate', RateLimit(5, 3600))
    original = None
    for user in accounts:
        request = {'username': user['username'], 'password': PASSWORD, 'operationId': str(uuid.uuid4())}
        reply = client.post('/api/v1/token', json=request)
        assert reply.status_code == 200, reply.text
        assert platform.wallets.balance(user['id']) == 100
        original = original or (user, request, reply.json())
    user, request, receipt = original
    for _ in range(5):
        reply = client.post('/api/v1/token', json={**request, 'password': 'wrong', 'operationId': str(uuid.uuid4())})
        assert reply.status_code == 400 and '密码错误' in reply.json()['error']['message']
    replay = client.post('/api/v1/token', json=request)
    assert replay.status_code == 200 and replay.json() == receipt
    assert platform.wallets.balance(user['id']) == 100
    assert platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=? AND kind=?',
                           user['id'], 'login')['n'] == 1
    blocked = client.post('/api/v1/token', json={**request, 'operationId': str(uuid.uuid4())})
    assert blocked.status_code == 400 and '频繁' in blocked.json()['error']['message']


def test_success_preserves_failures_and_unknown_accounts_consume_budget(auth):
    for name in ('absent-one', 'known-user', 'absent-two', 'known-user'):
        with pytest.raises(A2CError, match='账号不存在|密码错误'):
            auth.authenticate(name, 'wrong', ip='source-a')
    assert auth.authenticate('known-user', PASSWORD, ip='source-a')['username'] == 'known-user'
    with pytest.raises(A2CError, match='账号不存在'):
        auth.authenticate('absent-three', PASSWORD, ip='source-a')
    for name, password in (('known-user', PASSWORD), ('absent-four', 'wrong')):
        with pytest.raises(A2CError, match='频繁'):
            auth.authenticate(name, password, ip='source-a')
    assert auth.authenticate('known-user', PASSWORD, ip='source-b')['username'] == 'known-user'


def test_registration_login_and_admin_budgets_are_independent(auth):
    for index in range(5):
        auth.register('external-' + str(index), PASSWORD, ip='same-source')
    for _ in range(8):
        assert auth.authenticate('known-user', PASSWORD, ip='same-source')
        assert auth.verify(auth.admin_login(PASSWORD, ip='same-source')) == 'admin'
    with pytest.raises(A2CError, match='频繁'):
        auth.register('registration-full', PASSWORD, ip='same-source')
    for _ in range(5):
        with pytest.raises(A2CError, match='密码错误'):
            auth.authenticate('known-user', 'wrong', ip='same-source')
    # A full ordinary login budget does not consume the admin budget.
    assert auth.admin_login(PASSWORD, ip='same-source')
    for _ in range(5):
        with pytest.raises(A2CError, match='口令错误'):
            auth.admin_login('wrong', ip='same-source')
    with pytest.raises(A2CError, match='频繁'):
        auth.admin_login(PASSWORD, ip='same-source')
    assert auth.admin_login(PASSWORD, ip='another-source')
    assert auth.register('other-ip-registration', PASSWORD, ip='another-source')


def test_admin_http_success_does_not_spend_or_clear_failures(platform, client, monkeypatch):
    monkeypatch.setattr(platform.auth, 'rate', RateLimit(5, 3600))
    for _ in range(8):
        assert client.post('/api/admin/login', json={'username':'admin','password': 'test'}).status_code == 200
    for index in range(5):
        reply = client.post('/api/admin/login', json={'username':'admin','password': 'wrong'})
        assert reply.status_code == 400 and '口令错误' in reply.json()['error']['message']
        if index < 4:
            assert client.post('/api/admin/login', json={'username':'admin','password': 'test'}).status_code == 200
    reply = client.post('/api/admin/login', json={'username':'admin','password': 'test'})
    assert reply.status_code == 400 and '频繁' in reply.json()['error']['message']


def test_failure_window_releases_only_expired_entries(auth, monkeypatch):
    clock = [10000.0]
    monkeypatch.setattr(authentication.time, 'time', lambda: clock[0])
    with pytest.raises(A2CError, match='密码错误'):
        auth.authenticate('known-user', 'wrong', ip='source')
    clock[0] += 1
    for _ in range(4):
        with pytest.raises(A2CError, match='密码错误'):
            auth.authenticate('known-user', 'wrong', ip='source')
    clock[0] = 13599.999
    with pytest.raises(A2CError, match='频繁'):
        auth.authenticate('known-user', PASSWORD, ip='source')
    clock[0] = 13600.0
    assert auth.authenticate('known-user', PASSWORD, ip='source')
    with pytest.raises(A2CError, match='密码错误'):
        auth.authenticate('known-user', 'wrong', ip='source')
    with pytest.raises(A2CError, match='频繁'):
        auth.authenticate('known-user', 'wrong', ip='source')


def test_concurrent_password_checks_cannot_overspend_last_failure(auth):
    for _ in range(4):
        with pytest.raises(A2CError, match='密码错误'):
            auth.authenticate('known-user', 'wrong', ip='source')
    barrier = threading.Barrier(8)
    def attempt(_):
        barrier.wait(timeout=10)
        try:
            auth.authenticate('known-user', 'wrong', ip='source')
        except A2CError as error:
            return str(error)
        pytest.fail('Bad credentials were accepted')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum('密码错误' in result for result in results) == 1
    assert sum('频繁' in result for result in results) == 7


@pytest.mark.parametrize('failure', ['password-check', 'database'])
def test_internal_failure_is_not_charged_as_bad_credentials(auth, monkeypatch, failure):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError('internal failure')
    with monkeypatch.context() as patch:
        if failure == 'password-check':
            patch.setattr(authentication, '_check_pw', unavailable)
        else:
            patch.setattr(auth.db, 'one', unavailable)
        with pytest.raises(RuntimeError, match='internal failure'):
            auth.authenticate('known-user', PASSWORD, ip='source')
    for _ in range(5):
        with pytest.raises(A2CError, match='密码错误'):
            auth.authenticate('known-user', 'wrong', ip='source')
    with pytest.raises(A2CError, match='频繁'):
        auth.authenticate('known-user', PASSWORD, ip='source')
