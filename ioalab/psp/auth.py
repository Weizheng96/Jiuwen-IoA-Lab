"""账号：手机号注册（验证码）、密码、邀请码、会话 cookie（HMAC 签名）、管理员口令。

dev：验证码固定 123456 且页面显示，只绑 127.0.0.1，手机号只是登录名。
trial：禁固定码，注册必须设密码，按 IP 限速；IOALAB_REGISTRATION=invite 时还需管理员生成的一次性邀请码，closed 时不开放注册。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque

from a2c.errors import CARD_INVALID, fail

from ..config import Settings
from ..store import DB, now

FIXED_CODE = "123456"


def _pbkdf2(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def _check_pw(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
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
        pw = os.environ.get("IOALAB_ADMIN_PASSWORD")
        p = self.s.data / "admin.secret"
        if not pw:
            if not p.exists():
                pw = secrets.token_urlsafe(12)
                p.write_text(pw, encoding="utf-8")
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
                print(f"[ioalab] 管理员口令已生成：{pw}（存于 {p}）")
            pw = p.read_text(encoding="utf-8").strip()
        return _pbkdf2(pw)

    def admin_login(self, password: str) -> str:
        if not _check_pw(password, self.admin_hash):
            fail(CARD_INVALID, "口令错误")
        return self.token("admin")

    # ── 验证码与注册 ──────────────────────────────────────────────────
    def send_code(self, phone: str, ip: str) -> str | None:
        """dev 返回固定码（页面显示）；open 不提供验证码登录。"""
        if self.s.open:
            fail(CARD_INVALID, "开放模式不提供验证码")
        self.db.run("INSERT OR REPLACE INTO codes (phone, code, at) VALUES (?,?,?)", phone, FIXED_CODE, now())
        return FIXED_CODE

    def register(self, phone: str, *, code: str = "", password: str = "", invite: str = "", ip: str = "",
                 is_virtual: bool = False, note: str = "") -> dict:
        if not phone or len(phone) < 6:
            fail(CARD_INVALID, "手机号格式不对")
        if self.db.one("SELECT 1 FROM users WHERE phone=?", phone):
            fail(CARD_INVALID, "该手机号已注册")
        if not is_virtual and self.s.registration == "closed":
            fail(CARD_INVALID, "当前不开放注册")
        if self.s.open and not is_virtual:
            self.rate.check(ip)
            if len(password) < 8:
                fail(CARD_INVALID, "必须设置 8 位以上密码")
        elif not is_virtual:
            row = self.db.one("SELECT code FROM codes WHERE phone=?", phone)
            if not row or row["code"] != code:
                fail(CARD_INVALID, "验证码错误")
        need_invite = not is_virtual and self.s.registration == "invite"
        if need_invite and not self.db.one("SELECT 1 FROM invites WHERE code=? AND used_by IS NULL", invite):
            fail(CARD_INVALID, "邀请码无效")
        uid = "u-" + uuid.uuid4().hex[:10]
        with self.db.tx() as c:
            c.execute("INSERT INTO users (id, phone, pw_hash, created, is_virtual, note) VALUES (?,?,?,?,?,?)",
                      (uid, phone, _pbkdf2(password) if password else None, now(), int(is_virtual), note))
            if need_invite:
                c.execute("UPDATE invites SET used_by=? WHERE code=?", (uid, invite))
        return {"id": uid, "phone": phone}

    def login(self, phone: str, *, code: str = "", password: str = "", ip: str = "") -> dict:
        row = self.db.one("SELECT * FROM users WHERE phone=?", phone)
        if not row:
            fail(CARD_INVALID, "账号不存在")
        if self.s.open:
            self.rate.check(ip)
            if not _check_pw(password, row["pw_hash"]):
                fail(CARD_INVALID, "密码错误")
        else:
            ok = (password and _check_pw(password, row["pw_hash"])) or code == FIXED_CODE
            if not ok:
                fail(CARD_INVALID, "验证码或密码错误")
        return {"id": row["id"], "phone": row["phone"], "token": self.token(row["id"])}

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


__all__ = ["Auth", "FIXED_CODE"]
