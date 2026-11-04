"""Trial administrator initial credentials never use a public default."""
import pytest


def test_trial_administrator_password_is_generated_not_default(tmp_path):
    from ioalab.config import Settings
    from ioalab.app import Platform
    from ioalab.seed.accounts import ensure_accounts
    from a2c.errors import A2CError
    pf=Platform(Settings(data=tmp_path,mode='trial',admin_password='test-long-admin-password'))
    try:
        ensure_accounts(pf)
        password=(tmp_path/'assistant.secret').read_text().strip()
        assert len(password)>=12 and password!='001'
        u=pf.auth.login('ISC_001',password)
        assert pf.auth.is_admin(u['id'])
        with pytest.raises(A2CError):pf.auth.login('ISC_001','001')
    finally:
        pf.close()
