"""Identity owns its table definitions and additive upgrades."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_roles(user_id TEXT,role TEXT,PRIMARY KEY(user_id,role));
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE, pw_hash TEXT, kind TEXT DEFAULT 'external', display_name TEXT DEFAULT '', created REAL, last_login REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, owner_id TEXT, role TEXT, name TEXT, status TEXT DEFAULT 'online', created REAL, meta TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, created REAL, used_by TEXT);

CREATE TABLE IF NOT EXISTS connection_tickets(
 digest TEXT PRIMARY KEY,owner TEXT NOT NULL,actors TEXT NOT NULL,
 expires REAL NOT NULL,consumed INTEGER NOT NULL DEFAULT 0);


CREATE TABLE IF NOT EXISTS identity_keys(fingerprint TEXT PRIMARY KEY,owner TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identity_enrollments(owner TEXT,operation TEXT,actor TEXT NOT NULL,role TEXT NOT NULL,
 protocol TEXT NOT NULL,challenge TEXT NOT NULL,expires REAL NOT NULL,fingerprint TEXT,result TEXT,
 PRIMARY KEY(owner,operation));

CREATE TABLE IF NOT EXISTS account_operations(
                username TEXT,kind TEXT,id TEXT,params TEXT NOT NULL,owner TEXT NOT NULL,
                credential TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,
                PRIMARY KEY(username,kind,id));
CREATE TABLE IF NOT EXISTS identity_role_changes(
                id TEXT PRIMARY KEY,at REAL NOT NULL,operator TEXT NOT NULL,target TEXT NOT NULL,previous TEXT NOT NULL,current TEXT NOT NULL);
"""


def migrate(conn):
    from ..migrations import execute
    execute(conn, SCHEMA)


def release_service_listings(conn):
    """Hand the former service directory columns to the registry once, then drop them.

    Returns (agent id, bound protocol address, name, definition JSON) for services that had a listing. The
    identity status `listed` reverts to `registered`; listing state belongs to the registry.
    """
    if 'definition' not in {row['name'] for row in conn.execute('PRAGMA table_info(agents)')}:
        return []
    rows = [tuple(row) for row in conn.execute(
        "SELECT id,json_extract(meta,'$.url'),name,definition FROM agents WHERE role='service' AND json_valid(definition) "
        "AND json_type(definition,'$.category')='text' AND json_extract(definition,'$.category')<>''")]
    conn.execute("UPDATE agents SET status='registered' WHERE status='listed'")
    conn.execute('ALTER TABLE agents DROP COLUMN definition')
    return rows
