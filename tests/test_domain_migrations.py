"""Existing business data survives the single, transactional startup migration."""
from contextlib import contextmanager
import sqlite3

import pytest

from ioalab.migrations import migrate
from ioalab.storage import DB, DataOwner


def test_connection_has_no_domain_schema_and_complete_startup_is_repeatable(tmp_path):
    db = DB(tmp_path / 'core.sqlite')
    try:
        assert db.rows("SELECT name FROM sqlite_master WHERE type='table'") == []
        migrate(db)
        names = {row['name'] for row in db.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'users', 'user_roles', 'agents', 'invites', 'connection_tickets', 'identity_keys',
                'identity_enrollments', 'account_operations', 'identity_role_changes', 'wallets', 'wallet_log',
                'paid', 'transfers', 'payment_records', 'delegation_scopes', 'delegation_grants',
                'delegation_scope_operations', 'delegation_scope_payments', 'delegation_payment_operations',
                'needs', 'confirmations', 'rulings', 'admission_policies', 'delegation_intents',
                'delegation_links', 'delegation_admissions', 'admission_operations', 'admission_payments',
                'delegation_signed_requests', 'delegation_offers', 'delegation_contracts',
                'delegation_step_answers', 'delegation_termination_quotes', 'delegation_market_operations',
                'delegation_protocol_calls', 'arbitration_cases', 'arbitration_operations',
                'arbitration_attempts', 'arbitration_admin_operations', 'arbitration_audit',
                'registry_publications', 'registry_publication_operations', 'registry_projection',
                'registry_publication_authorizations', 'registry_authorization_operations',
                'delivery_leases', 'delivery_mailboxes', 'delivery_messages', 'delivery_ids',
                'delivery_interactions', 'delivery_closed_origins', 'delivery_operations', 'delivery_operation_ids',
                'message_interfaces', 'message_versions', 'events', 'observation_contracts',
                'private_files', 'private_file_operations', 'private_file_readers', 'kv'} <= names
        schema = db.rows("SELECT name,sql FROM sqlite_master ORDER BY name")
        migrate(db)
        assert db.rows("SELECT name,sql FROM sqlite_master ORDER BY name") == schema
    finally:
        db.close()


def test_upgrade_preserves_accounts_keys_money_rulings_and_original_contracts(tmp_path):
    path = tmp_path / 'core.sqlite'
    db = DB(path)
    migrate(db)
    # Stored facts are opaque originals: migration must not parse or rewrite them.
    rows = {
        'users': ("INSERT INTO users(id,username,pw_hash) VALUES('u','original','hash')", 'id'),
        'wallets': ("INSERT INTO wallets VALUES('u','12.34','original-key')", 'user_id'),
        'transfers': ("INSERT INTO transfers(at,payer_id,payee_id,amount,purpose,ref,payment_id) VALUES(1,'u','v','3.21','period','card','payment')", 'id'),
        'rulings': ("INSERT INTO rulings VALUES('card',0,1,'signed-ruling')", 'card_digest'),
        'kv': ("INSERT INTO kv VALUES('market','cards','card','original-bytes')", 'k'),
        'delegation_contracts': ("INSERT INTO delegation_contracts(admission,digest,card,progress) VALUES('a','card','signed-card',NULL)", 'admission'),
    }
    for sql, _ in rows.values():
        db.run(sql)
    # Recreate older additive shapes while retaining representative original records.
    db.run("CREATE TABLE needs_old(id TEXT PRIMARY KEY,owner_id TEXT,assistant_id TEXT,text TEXT,scope TEXT,auto_sign INTEGER DEFAULT 1,state TEXT DEFAULT 'queued',card_digest TEXT,result TEXT,created REAL,updated REAL,llm_calls INTEGER DEFAULT 0,tokens INTEGER DEFAULT 0,trace TEXT DEFAULT '[]')")
    db.run("INSERT INTO needs_old(id,text) VALUES('need','original need')")
    db.run('DROP TABLE needs')
    db.run('ALTER TABLE needs_old RENAME TO needs')
    db.run('ALTER TABLE delegation_contracts DROP COLUMN progress')
    db.run('ALTER TABLE arbitration_cases DROP COLUMN decision_basis')
    before = {table: db.rows('SELECT * FROM ' + table + ' ORDER BY ' + key) for table, (_, key) in rows.items()}
    db.close()
    db = DB(path)
    try:
        migrate(db)
        for table, old_rows in before.items():
            actual = db.rows('SELECT * FROM ' + table + ' ORDER BY ' + rows[table][1])
            assert [{key: row[key] for key in old_rows[0]} for row in actual] == old_rows
        assert db.one("SELECT text,driver FROM needs WHERE id='need'") == {'text': 'original need', 'driver': 'hosted'}
        assert db.one("SELECT progress FROM delegation_contracts WHERE admission='a'") == {'progress': None}
        assert 'decision_basis' in {row['name'] for row in db.rows('PRAGMA table_info(arbitration_cases)')}
        with pytest.raises(sqlite3.IntegrityError):
            db.run("INSERT INTO users(id,username) VALUES('other','original')")
    finally:
        db.close()


def test_failed_component_upgrade_rolls_back_all_ddl_and_releases_transaction(tmp_path, monkeypatch):
    from ioalab.observation import migrations as observation
    db = DB(tmp_path / 'core.sqlite')
    try:
        db.run('CREATE TABLE original(id TEXT PRIMARY KEY,value TEXT)')
        db.run("INSERT INTO original VALUES('id','preserved')")
        def fail(conn):
            conn.execute("UPDATE original SET value='corrupted'")
            raise OSError('injected migration interruption')
        with monkeypatch.context() as patch:
            patch.setattr(observation, 'migrate', fail)
            with pytest.raises(OSError, match='interruption'):
                migrate(db)
        assert db.one('SELECT * FROM original') == {'id': 'id', 'value': 'preserved'}
        assert db.rows("SELECT name FROM sqlite_master WHERE type='table'") == [{'name': 'original'}]
        assert not db.conn.in_transaction
        migrate(db)
        with db.tx() as conn:
            assert conn.execute('SELECT value FROM original').fetchone()[0] == 'preserved'
    finally:
        db.close()


def test_startup_migration_failure_releases_core_owner(tmp_path, monkeypatch):
    import ioalab.migrations as migrations
    from ioalab.app import Platform
    from ioalab.config import Settings
    def fail(db):
        raise OSError('migration failed')
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'migrate', fail)
        with pytest.raises(OSError, match='migration failed'):
            Platform(Settings(data=tmp_path, autoseed=False))
    with DataOwner(tmp_path):
        db = DB(tmp_path / 'ioalab.sqlite')
        try:
            migrate(db)
        finally:
            db.close()
