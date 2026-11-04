"""Control metadata only; never opens a core or SDK database."""
import contextlib
import sqlite3
import threading


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS queue(owner TEXT PRIMARY KEY, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS seats(id INTEGER PRIMARY KEY, state TEXT NOT NULL,
          epoch TEXT, owner TEXT UNIQUE, expires REAL, seen REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, owner TEXT NOT NULL,
          csrf TEXT NOT NULL, expires REAL NOT NULL, audience TEXT NOT NULL,
          purpose TEXT NOT NULL, seat INTEGER, epoch TEXT);
        ''')
        for seat in range(1, 4):
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
