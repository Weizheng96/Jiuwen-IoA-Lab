"""Messaging owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_leases(actor TEXT PRIMARY KEY,runtime TEXT,connection TEXT,generation INTEGER NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_mailboxes(actor TEXT PRIMARY KEY,last_seq INTEGER NOT NULL DEFAULT 0,ack_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS delivery_messages(id TEXT PRIMARY KEY,sender TEXT NOT NULL,recipient TEXT NOT NULL,seq INTEGER NOT NULL,kind TEXT NOT NULL,envelope TEXT,fingerprint TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,retain_until REAL NOT NULL,delivery_state TEXT NOT NULL,request_state TEXT NOT NULL,interaction TEXT,origin TEXT,in_reply TEXT,started REAL,start_generation INTEGER,result TEXT,result_id TEXT,result_hash TEXT,UNIQUE(recipient,seq));
CREATE TABLE IF NOT EXISTS delivery_ids(digest TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS ix_delivery_recipient ON delivery_messages(recipient,seq);
CREATE INDEX IF NOT EXISTS ix_delivery_interaction ON delivery_messages(interaction,kind);
CREATE TABLE IF NOT EXISTS delivery_interactions(id TEXT PRIMARY KEY,origin TEXT UNIQUE NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,interface TEXT NOT NULL,revision INTEGER NOT NULL,deadline REAL NOT NULL,max_requests INTEGER NOT NULL,requests INTEGER NOT NULL,state TEXT NOT NULL,closed_at REAL);
CREATE TABLE IF NOT EXISTS delivery_closed_origins(origin TEXT PRIMARY KEY,reason TEXT NOT NULL,closed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_operations(actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,retain_until REAL NOT NULL,PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS delivery_operation_ids(digest TEXT PRIMARY KEY);


CREATE TABLE IF NOT EXISTS message_interfaces(owner TEXT,name TEXT,revision INTEGER,definition TEXT,enabled INTEGER,updated REAL,PRIMARY KEY(owner,name));
CREATE TABLE IF NOT EXISTS message_versions(owner TEXT,name TEXT,revision INTEGER,definition TEXT,PRIMARY KEY(owner,name,revision));
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)
