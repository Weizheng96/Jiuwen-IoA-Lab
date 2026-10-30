"""Small participant-owned SQLite store; no shared platform database."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from a2c.common import Store


class LocalStore(Store):
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS documents (tbl TEXT,k TEXT,v TEXT,PRIMARY KEY(tbl,k))")

    @contextmanager
    def transaction(self):
        """Short bootstrap/cache updates shared by threads and processes, never network calls."""
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def put(self, table, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?)", (table,key,json.dumps(value,ensure_ascii=False)))

    def get(self, table, key, default=None):
        with self.lock:
            row=self.db.execute("SELECT v FROM documents WHERE tbl=? AND k=?", (table,key)).fetchone()
            return json.loads(row[0]) if row else default

    def all(self, table):
        with self.lock:
            return {r[0]:json.loads(r[1]) for r in self.db.execute("SELECT k,v FROM documents WHERE tbl=?", (table,)).fetchall()}

    def delete(self, table, key):
        with self.lock:
            self.db.execute("DELETE FROM documents WHERE tbl=? AND k=?", (table,key))

    def close(self):
        self.db.close()
