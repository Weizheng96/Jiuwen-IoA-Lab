"""Payments owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (user_id TEXT PRIMARY KEY, balance TEXT NOT NULL DEFAULT '0', payer_thumbprint TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS wallet_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, user_id TEXT, delta TEXT, balance TEXT, kind TEXT, ref TEXT, by TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS paid (ref TEXT, purpose TEXT, period INTEGER, at REAL, PRIMARY KEY (ref, purpose, period));
CREATE TABLE IF NOT EXISTS transfers (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, payer_id TEXT, payee_id TEXT, amount TEXT, purpose TEXT, ref TEXT, payment_id TEXT);

CREATE TABLE IF NOT EXISTS delegation_scopes(
 digest TEXT PRIMARY KEY,scope_id TEXT UNIQUE NOT NULL,owner TEXT NOT NULL,actor TEXT NOT NULL,
 context TEXT NOT NULL,revision INTEGER NOT NULL,issued REAL NOT NULL,expires REAL NOT NULL,closed INTEGER NOT NULL DEFAULT 0,
 fees TEXT NOT NULL DEFAULT '0.00',contract TEXT,total TEXT NOT NULL DEFAULT '0.00',paid TEXT NOT NULL DEFAULT '0.00');
CREATE TABLE IF NOT EXISTS delegation_grants(
 scope TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(scope,revision));
CREATE TABLE IF NOT EXISTS delegation_scope_operations(
 owner TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS delegation_scope_payments(
 payment_id TEXT PRIMARY KEY,scope TEXT NOT NULL,fingerprint TEXT NOT NULL,ref TEXT NOT NULL,purpose TEXT NOT NULL,
 amount TEXT NOT NULL,period INTEGER NOT NULL,UNIQUE(scope,ref,purpose,period));

CREATE TABLE IF NOT EXISTS payment_records(
 payment_id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,
 payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,provider TEXT NOT NULL,
 ref TEXT NOT NULL,purpose TEXT NOT NULL,period INTEGER NOT NULL,
 amount TEXT NOT NULL,currency TEXT NOT NULL,payer_key TEXT NOT NULL,
 reply TEXT NOT NULL,created REAL NOT NULL,UNIQUE(ref,purpose,period));
CREATE TABLE IF NOT EXISTS delegation_payment_operations(
 owner TEXT NOT NULL,actor TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
 PRIMARY KEY(actor,id));
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)
