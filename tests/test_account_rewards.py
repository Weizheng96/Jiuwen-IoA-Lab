"""Account creation and login grants share the owned transaction and calendar eligibility."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone

import pytest

from ioalab.config import Settings
from ioalab.identity.auth import Auth
from ioalab.migrations import migrate
from ioalab.operations import AccountOperations
from ioalab.payments.ledger import Wallets
from ioalab.storage import DB


@pytest.fixture
def accounts(tmp_path):
    settings=Settings(data=tmp_path,stub=True,autoseed=False)
    db=DB(tmp_path/'accounts.sqlite');migrate(db)
    wallets=Wallets(db);auth=Auth(db,settings)
    operations=AccountOperations(db,auth,wallets,settings,lambda *a,**kw:None)
    try:yield operations,wallets,settings,db
    finally:db.conn.close()


def test_default_initial_and_daily_login_credit_are_independent_and_idempotent(accounts):
    operations,wallets,settings,db=accounts
    first=operations.register('reward-user','test-password',operation_id='register-one')
    assert wallets.balance(first['id'])==600
    assert first['credits']['initial']=='500.00' and first['credits']['login']=='100.00'
    again=operations.login('reward-user','test-password',operation_id='login-two')
    assert again['credited'] is False and again['credits']['reason']=='already_claimed'
    assert wallets.balance(first['id'])==600
    settings.login_credit='900';settings.initial_credit='800'
    assert operations.register('reward-user','test-password',operation_id='register-one')==first
    assert operations.login('reward-user','test-password',operation_id='login-two')==again
    assert wallets.balance(first['id'])==600
    assert len(db.rows("SELECT * FROM wallet_log WHERE user_id=?",first['id']))==2


def test_concurrent_new_login_operations_share_one_daily_reward_and_policy_toggle(accounts):
    operations,wallets,settings,db=accounts
    user=operations.create_account('concurrent-user','test-password')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda i:operations.login('concurrent-user','test-password',operation_id=f'parallel-{i}'),range(8)))
    assert sum(row['credited'] for row in results)==1
    assert wallets.balance(user['id'])==600
    settings.repeat_daily_credit=True
    assert operations.login('concurrent-user','test-password',operation_id='repeat-enabled')['credited'] is True
    settings.repeat_daily_credit=False
    assert operations.login('concurrent-user','test-password',operation_id='repeat-disabled')['credited'] is False
    assert wallets.balance(user['id'])==700


def test_business_day_is_shanghai_and_old_operation_does_not_claim_next_day(accounts,monkeypatch):
    operations,wallets,settings,db=accounts
    before=datetime(2026,11,5,15,59,59,tzinfo=timezone.utc).timestamp()
    clock=[before]
    monkeypatch.setattr('ioalab.payments.ledger.now',lambda:clock[0])
    first=operations.register('day-user','test-password',operation_id='day-registration')
    assert first['credits']['day']=='2026-11-05'
    clock[0]+=2
    assert operations.register('day-user','test-password',operation_id='day-registration')==first
    next_day=operations.login('day-user','test-password',operation_id='next-day-login')
    assert next_day['credits']['day']=='2026-11-06' and next_day['credited'] is True
    assert wallets.balance(first['id'])==700


def test_failed_account_transaction_rolls_back_initial_and_day_qualification(accounts):
    operations,wallets,settings,db=accounts
    db.run("CREATE TRIGGER reject_operation BEFORE INSERT ON account_operations BEGIN SELECT RAISE(ABORT,'controlled failure'); END")
    with pytest.raises(Exception):operations.register('rollback-user','test-password',operation_id='rollback-register')
    assert db.one('SELECT 1 FROM users WHERE username=?','rollback-user') is None
    assert db.rows('SELECT * FROM wallets')==[] and db.rows('SELECT * FROM login_reward_days')==[]
    db.run('DROP TRIGGER reject_operation')
    result=operations.register('rollback-user','test-password',operation_id='rollback-register')
    assert wallets.balance(result['id'])==600


def test_historical_positive_login_ledger_establishes_day_without_new_initial_grant(accounts):
    operations,wallets,settings,db=accounts
    settings.initial_credit='0'
    user=operations.create_account('legacy-user','test-password')
    with db.tx() as conn:wallets.adjust_in(conn,user['id'],'25.00',kind='login',ref='original-legacy-login',by=user['id'],note='historical grant')
    before=db.rows('SELECT * FROM wallet_log WHERE user_id=?',user['id'])
    from ioalab.migrations import migrate
    migrate(db)
    settings.initial_credit='500'
    receipt=operations.login('legacy-user','test-password',operation_id='after-upgrade-login')
    assert not receipt['credited'] and receipt['credits']['reason']=='already_claimed'
    assert wallets.balance(user['id'])==25
    assert db.rows('SELECT * FROM wallet_log WHERE user_id=?',user['id'])==before
    assert len(db.rows('SELECT * FROM login_reward_days WHERE user_id=?',user['id']))==1
