"""Registry owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS registry_publication_authorizations(
 actor TEXT PRIMARY KEY,revision INTEGER NOT NULL,terms TEXT NOT NULL,lifetime INTEGER NOT NULL,
 enabled INTEGER NOT NULL,operator TEXT NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS registry_authorization_operations(
 id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);


CREATE TABLE IF NOT EXISTS registry_publications(
 actor TEXT,revision INTEGER,digest TEXT NOT NULL,body TEXT NOT NULL,templates TEXT NOT NULL,
 created REAL NOT NULL,PRIMARY KEY(actor,revision));
CREATE TABLE IF NOT EXISTS registry_publication_operations(
 actor TEXT,operation TEXT,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor,operation));
CREATE TABLE IF NOT EXISTS registry_projection(
 actor TEXT PRIMARY KEY,revision INTEGER NOT NULL,category TEXT NOT NULL,indexed REAL NOT NULL);
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)
