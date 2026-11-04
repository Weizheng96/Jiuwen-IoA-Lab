"""SQLite连接、短事务与共用存储适配；A2C Store 通过原协议kv适配保存。

DataOwner 保护整个 Core 数据目录；DB 是普通连接，事务用进程内锁串行，可在工作线程访问。
"""
from __future__ import annotations

import copy
import json
import errno
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from a2c.common import Store



class DataInUse(RuntimeError):
    """Another Core role owns this directory, independent of ordinary DB connections."""


class DataOwner:
    """Hold the same OS lock before any Core private file or database modification."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory.resolve() / '.core.lock'
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            os.close(self._fd)
            self._fd = None
            if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise DataInUse('Core data directory is already in use: ' + str(directory)) from exc
            raise

    def close(self) -> None:
        if self._fd is None:
            return
        descriptor = self._fd
        if os.name == 'nt':
            import msvcrt
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class DB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            self.conn.close()
            raise

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
        with db.lock:
            initialize_adapter(db.conn)
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


def initialize_adapter(conn):
    """Physical backing for the common A2C key/value adapter."""
    conn.execute("CREATE TABLE IF NOT EXISTS kv (party TEXT, tbl TEXT, k TEXT, v TEXT, PRIMARY KEY (party, tbl, k))")
