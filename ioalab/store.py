"""SQLite：平台表（账号 · 钱包 · 智能体 · 需求 · 订单 · 事件 · 过账）与各方 A2C Store 的 kv 表。

单进程持有：WAL · busy_timeout · 进程内单写锁；所有访问可在工作线程里发生（check_same_thread=False）。
"""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from a2c.common import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE, pw_hash TEXT, kind TEXT DEFAULT 'external', display_name TEXT DEFAULT '', created REAL, last_login REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS wallets (user_id TEXT PRIMARY KEY, balance TEXT NOT NULL DEFAULT '0', payer_thumbprint TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS wallet_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, user_id TEXT, delta TEXT, balance TEXT, kind TEXT, ref TEXT, by TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, owner_id TEXT, role TEXT, name TEXT, definition TEXT, status TEXT DEFAULT 'online', created REAL, meta TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS needs (id TEXT PRIMARY KEY, owner_id TEXT, assistant_id TEXT, text TEXT, scope TEXT, auto_sign INTEGER DEFAULT 1, state TEXT DEFAULT 'queued', card_digest TEXT, result TEXT, created REAL, updated REAL, llm_calls INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, trace TEXT DEFAULT '[]', driver TEXT DEFAULT 'hosted');
CREATE TABLE IF NOT EXISTS orders (card_digest TEXT PRIMARY KEY, need_id TEXT, owner_id TEXT, assistant_id TEXT, provider_id TEXT, template_digest TEXT, formation TEXT, total TEXT, state TEXT, phase TEXT, created REAL, updated REAL, detail TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, run_id TEXT, kind TEXT, op TEXT, sender TEXT, receiver TEXT, card_digest TEXT, period INTEGER, amount TEXT, outcome TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS paid (ref TEXT, purpose TEXT, period INTEGER, at REAL, PRIMARY KEY (ref, purpose, period));
CREATE TABLE IF NOT EXISTS transfers (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, payer_id TEXT, payee_id TEXT, amount TEXT, purpose TEXT, ref TEXT, payment_id TEXT);
CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, created REAL, used_by TEXT);
CREATE TABLE IF NOT EXISTS confirmations (id TEXT PRIMARY KEY, owner_id TEXT, need_id TEXT, card_digest TEXT, kind TEXT, text TEXT, state TEXT DEFAULT 'pending', created REAL, answered REAL);
CREATE TABLE IF NOT EXISTS kv (party TEXT, tbl TEXT, k TEXT, v TEXT, PRIMARY KEY (party, tbl, k));
CREATE TABLE IF NOT EXISTS rulings (card_digest TEXT, period INTEGER, at REAL, ruling TEXT, PRIMARY KEY (card_digest, period));
CREATE TABLE IF NOT EXISTS certifications (agent_id TEXT PRIMARY KEY, at REAL, record TEXT);
CREATE INDEX IF NOT EXISTS ix_events_card ON events (card_digest);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id);
"""


class DB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """老库补列（新列都带默认值，CREATE TABLE IF NOT EXISTS 不会改已有表）。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(needs)").fetchall()}
        if "driver" not in cols:
            self.conn.execute("ALTER TABLE needs ADD COLUMN driver TEXT DEFAULT 'hosted'")

    # ── 事务 ────────────────────────────────────────────────────────────
    class _Tx:
        def __init__(self, db: "DB") -> None:
            self.db = db

        def __enter__(self):
            self.db.lock.acquire()
            self.db.conn.execute("BEGIN IMMEDIATE")
            return self.db.conn

        def __exit__(self, et, ev, tb):
            try:
                self.db.conn.execute("ROLLBACK" if et else "COMMIT")
            finally:
                self.db.lock.release()

    def tx(self) -> "DB._Tx":
        return DB._Tx(self)

    def run(self, sql: str, *args: Any) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    def one(self, sql: str, *args: Any) -> dict | None:
        row = self.run(sql, *args).fetchone()
        return dict(row) if row else None

    def rows(self, sql: str, *args: Any) -> list[dict]:
        return [dict(r) for r in self.run(sql, *args).fetchall()]

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class SqliteStore(Store):
    """a2c.common.Store 的 SQLite 实现：一方一个 ``party`` 前缀，表与键落在 kv 表里。"""

    def __init__(self, db: DB, party: str) -> None:
        self._db = db
        self._party = party
        self._lock = threading.RLock()
        self._path = None
        self._data = {}

    def put(self, table: str, key: str, value: Any) -> None:
        self._db.run("INSERT OR REPLACE INTO kv (party, tbl, k, v) VALUES (?, ?, ?, ?)",
                     self._party, table, key, json.dumps(value, ensure_ascii=False))

    def get(self, table: str, key: str, default: Any = None) -> Any:
        row = self._db.one("SELECT v FROM kv WHERE party=? AND tbl=? AND k=?", self._party, table, key)
        return json.loads(row["v"]) if row else copy.deepcopy(default)

    def all(self, table: str) -> dict:
        return {r["k"]: json.loads(r["v"]) for r in self._db.rows("SELECT k, v FROM kv WHERE party=? AND tbl=?",
                                                                 self._party, table)}

    def delete(self, table: str, key: str) -> None:
        self._db.run("DELETE FROM kv WHERE party=? AND tbl=? AND k=?", self._party, table, key)


def now() -> float:
    return time.time()


def jload(s: str | None, default: Any = None) -> Any:
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except ValueError:
        return default if default is not None else {}


def jdump(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)


__all__ = ["DB", "SqliteStore", "now", "jload", "jdump"]
