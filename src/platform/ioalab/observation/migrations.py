"""Observation owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, run_id TEXT, kind TEXT, op TEXT, sender TEXT, receiver TEXT, card_digest TEXT, period INTEGER, amount TEXT, outcome TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_events_card ON events (card_digest);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id);
CREATE TABLE IF NOT EXISTS observation_contracts(
 admission TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,card TEXT NOT NULL,card_hash TEXT NOT NULL,
 effective INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)
