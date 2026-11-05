"""Trial administrator initial credentials never use a public default."""
import pytest


def test_trial_administrator_password_is_generated_not_default(tmp_path):
    from ioalab.config import Settings
    from ioalab.app import Platform
    from ioalab.seed.accounts import ensure_accounts
    from ioalab.errors import LabError
    from pathlib import Path
    pf=Platform(Settings(data=tmp_path,mode='trial',admin_password='test-long-admin-password',
        seed_file=Path(__file__).resolve().parents[1]/'deploy/default-seed.json',stub=True))
    try:
        ensure_accounts(pf)
        password=(tmp_path/'assistant.secret').read_text().strip()
        assert len(password)>=12 and password!='001'
        u=pf.auth.login('ISC_001',password)
        assert pf.auth.is_admin(u['id'])
        with pytest.raises(LabError) as error:pf.auth.login('ISC_001','001')
        assert error.value.code=='authenticate' and error.value.status==401
        assert error.value.recovery=='account.login'
    finally:
        pf.close()
