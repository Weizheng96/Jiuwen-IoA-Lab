"""Installed deployment probes must exercise the actual bootstrap authority."""
from urllib.error import HTTPError
import pytest
from tools.verification.verify_deployment import seed_accounts


def test_probe_seeds_through_bootstrap_cookie_without_creating_or_crediting_a_user(platform):
    assert platform.db.one("SELECT id FROM users WHERE username='admin'") is None
    before=platform.db.one("SELECT COUNT(*) AS n FROM wallet_log WHERE kind='login'")['n']
    seeded=seed_accounts(platform.s.base_url,platform.s.admin_password)
    assert seeded['isp']==1 and seeded['isc']==platform.s.isc_count
    assert platform.db.one("SELECT id FROM users WHERE username='admin'") is None
    assert platform.db.one("SELECT COUNT(*) AS n FROM wallet_log WHERE kind='login'")['n']==before


def test_probe_wrong_bootstrap_password_does_not_seed_or_fall_back_to_user_login(platform):
    before=platform.db.one('SELECT COUNT(*) AS n FROM users')['n']
    with pytest.raises(HTTPError) as error:
        seed_accounts(platform.s.base_url,'incorrect-isolated-bootstrap-password')
    assert error.value.code==401
    assert '口令错误' in __import__('json').load(error.value)['error']['message']
    assert platform.db.one('SELECT COUNT(*) AS n FROM users')['n']==before
