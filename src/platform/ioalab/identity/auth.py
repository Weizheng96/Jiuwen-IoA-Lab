"""身份中心：用户名密码注册登录、邀请码、会话 token（HMAC）、管理员口令。

账号种类 kind：isp（内部服务提供者）· isc（内部服务使用者）· external（外部用户）· virtual（管理员临时创建）。
种类只是用途标签，任何账号名下既可有个人助手也可有专业服务。
dev：无限速、密码 ≥ 6；trial：按 IP 限速、密码 ≥ 8。IOALAB_REGISTRATION=open｜invite｜closed 只约束 external 注册。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque

from a2c.errors import CARD_INVALID, fail

from ..config import Settings
from ..store import DB, now

USERNAME = re.compile(r"^[A-Za-z0-9_.@+-]{3,128}$")
KINDS = ("isp", "isc", "external", "virtual")
INTERNAL = ("isp", "isc", "virtual")


def _pbkdf2(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def _check_pw(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored or not password:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(_pbkdf2(password, base64.b64decode(salt)), stored)


class RateLimit:
    def __init__(self, n: int, per: float) -> None:
        self.n, self.per = n, per
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        t = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < t - self.per:
                q.popleft()
            if len(q) >= self.n:
                fail(CARD_INVALID, "操作过于频繁，请稍后再试")
            q.append(t)


class Auth:
    def __init__(self, db: DB, settings: Settings) -> None:
        self.db = db
        self.s = settings
        self.secret = settings.secret()
        self.rate = RateLimit(5, 3600)
        self.admin_hash = self._admin_hash()

    # ── 管理员 ───────────────────────────────────────────────────────
    def _admin_hash(self) -> str:
        pw = self.s.admin_password
        p = self.s.data / "admin.secret"
        if not pw:
            if not p.exists():
                pw = secrets.token_urlsafe(12)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(pw, encoding="utf-8")
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
                print(f"[ioalab] 管理员口令已生成并保存于 {p}")
            pw = p.read_text(encoding="utf-8").strip()
        return _pbkdf2(pw)

    def is_admin(self,subject):
        return subject=='admin' or bool(subject and not subject.startswith('assistant:') and
                self.db.one("SELECT 1 FROM user_roles WHERE user_id=? AND role='admin'",subject))

    def roles(self,uid):
        return [r['role'] for r in self.db.rows('SELECT role FROM user_roles WHERE user_id=?',uid)]

    def admin_login(self, password: str) -> str:
        if not _check_pw(password, self.admin_hash):
            fail(CARD_INVALID, "口令错误")
        return self.token("admin")

    # ── 注册与登录 ────────────────────────────────────────────────────
    def register(self, username: str, password: str = "", *, kind: str = "external", display_name: str = "", note: str = "",
                 invite: str = "", ip: str = "") -> dict:
        username = (username or "").strip()
        if not USERNAME.match(username):
            fail(CARD_INVALID, "用户名须为 3–128 位字母、数字或 _ . @ + -")
        if kind not in KINDS:
            fail(CARD_INVALID, f"账号种类不合法：{kind}")
        if self.db.one("SELECT 1 FROM users WHERE username=?", username):
            fail(CARD_INVALID, "用户名已被占用")
        external = kind == "external"
        if external:
            if self.s.registration == "closed":
                fail(CARD_INVALID, "当前不开放注册")
            if self.s.open:
                self.rate.check(ip)
            min_len = 8 if self.s.open else 6
            if len(password) < min_len:
                fail(CARD_INVALID, f"密码至少 {min_len} 位")
        need_invite = external and self.s.registration == "invite"
        if need_invite and not self.db.one("SELECT 1 FROM invites WHERE code=? AND used_by IS NULL", invite):
            fail(CARD_INVALID, "邀请码无效")
        uid = "u-" + uuid.uuid4().hex[:10]
        display = (display_name or "").strip() or username
        with self.db.tx() as c:
            c.execute("INSERT INTO users (id, username, pw_hash, kind, display_name, created, note) VALUES (?,?,?,?,?,?,?)",
                      (uid, username, _pbkdf2(password) if password else None, kind, display, now(), note))
            if need_invite:
                c.execute("UPDATE invites SET used_by=? WHERE code=?", (uid, invite))
        return {"id": uid, "username": username, "kind": kind, "display_name": display}

    def login(self, username: str, password: str, *, ip: str = "") -> dict:
        row = self.db.one("SELECT * FROM users WHERE username=?", (username or "").strip())
        if not row:
            fail(CARD_INVALID, "账号不存在")
        if self.s.open:
            self.rate.check(ip)
        if not _check_pw(password, row["pw_hash"]):
            fail(CARD_INVALID, "密码错误")
        self.db.run("UPDATE users SET last_login=? WHERE id=?", now(), row["id"])
        return {"id": row["id"], "username": row["username"], "kind": row["kind"], "display_name": row["display_name"],
                "token": self.token(row["id"])}

    def new_invite(self) -> str:
        code = secrets.token_urlsafe(8)
        self.db.run("INSERT INTO invites (code, created) VALUES (?,?)", code, now())
        return code

    # ── 会话 token（HMAC） ───────────────────────────────────────────
    def token(self, subject: str, ttl: int = 7 * 86400) -> str:
        exp = int(time.time()) + ttl
        msg = f"{subject}|{exp}"
        sig = hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()

    def bearer(self, subject: str, *, restricted=False) -> str:
        """脚本用的长效 token（30 天），与 cookie 同一算法。"""
        return self.token(("assistant:" if restricted else "")+subject, ttl=30 * 86400)

    def verify(self, token: str | None) -> str | None:
        """返回 subject（用户 id 或 "admin"），无效返回 None。"""
        if not token:
            return None
        try:
            subject, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().split("|")
        except Exception:  # noqa: BLE001
            return None
        want = hmac.new(self.secret, f"{subject}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want) or int(exp) < time.time():
            return None
        return subject


__all__ = ["Auth", "KINDS", "INTERNAL", "USERNAME"]
