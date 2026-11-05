"""Control metadata only; never opens a core or SDK database."""
import contextlib
import sqlite3
import threading
from .profile import capacity


class Store:
    def __init__(self, path, *, deployment_profile):
        self.profile=deployment_profile
        self.capacity=capacity(deployment_profile)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            tables={row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if 'deployment' not in tables:
                    raise ValueError('Original control database has no deployment profile; preserve it, fully retire its old epochs before using a new control_directory')
                bound=self.db.execute('SELECT profile,capacity FROM deployment').fetchall()
                ids=[row[0] for row in self.db.execute('SELECT id FROM seats ORDER BY id')]
                if [tuple(row) for row in bound]!=[(self.profile,self.capacity)] or ids!=list(range(1,self.capacity+1)):
                    raise ValueError('Control database deployment/capacity mismatch; preserve all old epochs and use a new control_directory only after full retirement')
        except BaseException:
            self.db.close()
            raise
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS queue(owner TEXT PRIMARY KEY, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS seats(id INTEGER PRIMARY KEY, state TEXT NOT NULL,
          epoch TEXT, owner TEXT UNIQUE, expires REAL, seen REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, owner TEXT NOT NULL,
          csrf TEXT NOT NULL, expires REAL NOT NULL, audience TEXT NOT NULL,
          purpose TEXT NOT NULL, seat INTEGER, epoch TEXT);
        CREATE TABLE IF NOT EXISTS deployment(profile TEXT PRIMARY KEY,capacity INTEGER NOT NULL);
        ''')
        self.db.execute('INSERT OR IGNORE INTO deployment VALUES(?,?)',(self.profile,self.capacity))
        for seat in range(1, self.capacity+1):
            self.db.execute("INSERT OR IGNORE INTO seats(id,state) VALUES(?,'preparing')", (seat,))

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def seats(self):
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT * FROM seats ORDER BY id')]

    def close(self):
        self.db.close()
