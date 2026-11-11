"""Observation owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS observation_contracts(
 admission TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,card TEXT NOT NULL,card_hash TEXT NOT NULL,
 effective INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)
