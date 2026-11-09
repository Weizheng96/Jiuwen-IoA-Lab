"""Registry owns its table definitions and additive upgrades."""
import json

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
CREATE TABLE IF NOT EXISTS registry_services(
 actor TEXT PRIMARY KEY,protocol TEXT,name TEXT NOT NULL,description TEXT NOT NULL,tags TEXT NOT NULL,
 category TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS registry_services_protocol ON registry_services(protocol);
"""


def migrate(conn):
    from ..migrations import execute
    from ..identity.migrations import release_service_listings
    execute(conn, SCHEMA)
    for actor, protocol, name, definition in release_service_listings(conn):
        listing = json.loads(definition)
        tags = listing.get('tags') if isinstance(listing.get('tags'), list) else []
        conn.execute('INSERT OR IGNORE INTO registry_services VALUES(?,?,?,?,?,?)',
                     (actor, protocol, listing.get('name') or name or actor, str(listing.get('description') or ''),
                      json.dumps(tags, ensure_ascii=False), listing['category']))
