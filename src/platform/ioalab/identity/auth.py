"""身份中心：用户名密码注册登录、邀请码、会话 token（HMAC）、管理员口令。

账号种类 kind：isp（内部服务提供者）· isc（内部服务使用者）· external（外部用户）· virtual（管理员临时创建）。
种类只是用途标签，任何账号名下既可有个人助手也可有专业服务。
dev：普通用户登录无限速、密码 ≥ 6；trial：按 IP 限速、密码 ≥ 8。runtime.registration只约束external注册。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque

from a2c.errors import CARD_INVALID, fail

from ..errors import LabError
from ..config import Settings
from ..storage import DB, now

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

    def _available(self, key: str, n=None, per=None) -> deque:
        """Caller owns the lock; expired hits cannot consume a new window."""
        t = time.time()
        q = self.hits[key]
        retention=self.per if per is None else 86400
        while q and q[0] <= t-retention:
            q.popleft()
        window=self.per if per is None else per
        limit=self.n if n is None else n
        if sum(hit>t-window for hit in q) >= limit:
            raise LabError('rate_limited', '操作过于频繁，请稍后再试', status=429, recovery='wait_rate_limit')
        return q

    def check(self, key: str, *, n=None, per=None) -> None:
        with self.lock:
            self._available(key,n,per).append(time.time())

    def check_failure(self, key: str, verify, *, n=None, per=None) -> bool:
        """Serialize synchronous verification and charge only a known failure.

        Success preserves earlier failures. Exceptions propagate without being
        classified as bad credentials; no concurrent check can spend the same
        remaining failure slot while verification is in progress.
        """
        with self.lock:
            q = self._available(key,n,per)
            valid = verify()
            if not valid:
                q.append(time.time())
            return valid


class Auth:
    def __init__(self, db: DB, settings: Settings) -> None:
        self.db = db
        self.s = settings
        self.secret = settings.secret()
        self.rate = RateLimit(settings.auth_max_failures,settings.auth_window_seconds)
        self.admin_hash = self._admin_hash()

    # ── 管理员 ───────────────────────────────────────────────────────
    def _admin_hash(self) -> str:
        if not self.s.admin_username or not self.s.admin_password:
            raise ValueError('Configure the existing administrator credentials explicitly')
        return _pbkdf2(self.s.admin_password)

    def is_admin(self,subject):
        return subject=='admin' or bool(subject and not subject.startswith('assistant:') and
                self.db.one("SELECT 1 FROM user_roles WHERE user_id=? AND role='admin'",subject))

    def next_virtual_name(self):
        return "virtual_{:03d}".format(self.db.one("SELECT COUNT(*) AS n FROM users")["n"]+1)

    def roles(self,uid):
        return [r['role'] for r in self.db.rows('SELECT role FROM user_roles WHERE user_id=?',uid)]

    def is_auditor(self,subject):
        return bool(subject and subject!='admin' and self.is_admin(subject) and 'auditor' in self.roles(subject))

    def grant_admin_in(self,conn,user_id):
        """Deployment preset: add the admin role inside the caller's transaction; never removes roles."""
        return conn.execute("INSERT OR IGNORE INTO user_roles(user_id,role) VALUES(?, 'admin')",(user_id,)).rowcount==1

    def set_roles(self,operator,target,roles,expected):
        from ..errors import LabError
        def valid(value):return isinstance(value,list) and len(value)<=2 and all(type(x) is str and x in ('admin','auditor') for x in value) and len(set(value))==len(value)
        if not valid(roles) or not valid(expected) or ('auditor' in roles and 'admin' not in roles):
            raise LabError('invalid_input','Auditor permission requires an explicit web administrator role')
        with self.db.tx() as conn:
            if not self.is_admin(operator):raise LabError('forbidden','Web administrator permission is required',status=403)
            if not conn.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone():raise LabError('not_ready','Target account does not exist',status=404)
            current=sorted(row['role'] for row in conn.execute('SELECT role FROM user_roles WHERE user_id=?',(target,)))
            desired=sorted(roles)
            if current==desired:return {'userId':target,'roles':current,'changed':False}
            if current!=sorted(expected):raise LabError('revision_conflict','Read current roles before replacing them',status=409)
            conn.execute('DELETE FROM user_roles WHERE user_id=?',(target,))
            conn.executemany('INSERT INTO user_roles(user_id,role) VALUES(?,?)',[(target,role) for role in desired])
            import json
            conn.execute('INSERT INTO identity_role_changes VALUES(?,?,?,?,?,?)',
                (str(uuid.uuid4()),time.time(),operator,target,json.dumps(current),json.dumps(desired)))
            return {'userId':target,'roles':desired,'previousRoles':current,'changed':True}

    def local_frequency_exempt(self,ip,policy):
        try:
            address=ipaddress.ip_address(ip)
            address=getattr(address,'ipv4_mapped',None) or address
            local=str(address) in self.s.local_addresses
        except ValueError:local=False
        return policy['authentication']['local_unlimited'] and local

    def admin_login(self, password: str, *, username: str = "admin", ip: str = "") -> str:
        if not isinstance(username,str) or not isinstance(password,str):fail(CARD_INVALID,"账号字段必须是字符串")
        policy=self.s.policy(); auth=policy['authentication']
        verify=lambda: username==self.s.admin_username and _check_pw(password,self.admin_hash)
        valid=verify() if self.local_frequency_exempt(ip,policy) else self.rate.check_failure('admin:'+ip,verify,n=auth['max_failures'],per=auth['window_seconds'])
        if not valid:raise LabError('authenticate','口令错误',status=401,recovery='account.login')
        return self.token('admin')

    def configuration_csrf(self, token: str) -> str:
        """Bind configuration writes to an authenticated browser session."""
        return hmac.new(self.secret, ('platform-configuration:'+token).encode(), hashlib.sha256).hexdigest()

    # ── 注册与登录 ────────────────────────────────────────────────────
    def prepare_registration(self, username: str, password: str = "", *, kind: str = "external", display_name: str = "", note: str = "",
                 invite: str = "", ip: str = "") -> dict:
        if not all(isinstance(value,str) for value in (username,password,display_name,note,invite)):
            fail(CARD_INVALID,'账号字段必须是字符串')
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
            policy=self.s.policy(); limits=policy["authentication"]
            if self.s.open and not self.local_frequency_exempt(ip,policy):
                self.rate.check("register:" + ip,n=limits["registration_limit"],per=limits["registration_window_seconds"])
            min_len = 8 if self.s.open else 6
            if len(password) < min_len:
                fail(CARD_INVALID, f"密码至少 {min_len} 位")
        need_invite = external and self.s.registration == "invite"
        if need_invite and not self.db.one("SELECT 1 FROM invites WHERE code=? AND used_by IS NULL", invite):
            fail(CARD_INVALID, "邀请码无效")
        uid = "u-" + uuid.uuid4().hex[:10]
        display = (display_name or "").strip() or username
        return {'id':uid,'username':username,'pw_hash':_pbkdf2(password) if password else None,
                'kind':kind,'display_name':display,'created':now(),'note':note,'invite':invite,'need_invite':need_invite}

    @staticmethod
    def public_user(row):
        return {key:row[key] for key in ('id','username','kind','display_name')}

    def register_in(self,c,prepared):
        if c.execute('SELECT 1 FROM users WHERE username=?',(prepared['username'],)).fetchone():
            fail(CARD_INVALID,'用户名已被占用')
        if prepared['need_invite']:
            changed=c.execute('UPDATE invites SET used_by=? WHERE code=? AND used_by IS NULL',(prepared['id'],prepared['invite']))
            if changed.rowcount!=1:fail(CARD_INVALID,'邀请码无效')
        c.execute('INSERT INTO users(id,username,pw_hash,kind,display_name,created,note) VALUES(?,?,?,?,?,?,?)',
                  tuple(prepared[key] for key in ('id','username','pw_hash','kind','display_name','created','note')))
        return self.public_user(prepared)

    def register(self,username,password='',**kwargs):
        prepared=self.prepare_registration(username,password,**kwargs)
        with self.db.tx() as c:
            return self.register_in(c,prepared)

    def authenticate(self, username: str, password: str, *, ip: str = "", policy=None) -> dict:
        if not isinstance(username,str) or not isinstance(password,str):
            fail(CARD_INVALID,'账号字段必须是字符串')
        row = self.db.one("SELECT * FROM users WHERE username=?", (username or "").strip())
        verify = lambda: bool(row and _check_pw(password, row["pw_hash"]))
        policy=policy or self.s.policy(); limits=policy["authentication"]
        valid = self.rate.check_failure("login:" + ip, verify,n=limits["max_failures"],per=limits["window_seconds"]) if self.s.open and not self.local_frequency_exempt(ip,policy) else verify()
        if not row:
            raise LabError('authenticate', '账号或密码错误', status=401, recovery='account.login')
        if not valid:
            raise LabError('authenticate', '账号或密码错误', status=401, recovery='account.login')
        return row

    def login_in(self,conn,verified):
        current=conn.execute('SELECT pw_hash FROM users WHERE id=?',(verified['id'],)).fetchone()
        if not current or current['pw_hash']!=verified['pw_hash']:
            raise LabError('authenticate','账号凭据已变更，请重新登录',status=401,recovery='account.login')
        conn.execute('UPDATE users SET last_login=? WHERE id=?',(now(),verified['id']))
        return self.public_user(verified)

    def login(self,username,password,*,ip=''):
        verified=self.authenticate(username,password,ip=ip)
        with self.db.tx() as conn:
            user=self.login_in(conn,verified)
        return {**user,'token':self.token(user['id'])}

    def is_preset_account(self, username, kind):
        row = self.db.one('SELECT kind,note FROM users WHERE username=?', username)
        return bool(row and row['kind'] == kind and row['note'] == '部署自带')

    def normalize_preset_display_name_in(self, conn, username, kind):
        """Migrate only a manifest-selected account with deployment provenance."""
        return conn.execute(
            "UPDATE users SET display_name=username WHERE username=? AND kind=? "
            "AND note='部署自带' AND display_name<>username", (username, kind)
        ).rowcount == 1

    def reset_default_password_in(self,conn,username,current,replacement):
        """Replace a preset account's still-default password inside the caller's transaction."""
        row=conn.execute('SELECT id,pw_hash FROM users WHERE username=?',(username,)).fetchone()
        if not row or not _check_pw(current,row['pw_hash']):
            return False
        conn.execute('UPDATE users SET pw_hash=? WHERE id=?',(_pbkdf2(replacement),row['id']))
        return True

    def new_invite(self) -> str:
        code = secrets.token_urlsafe(8)
        self.db.run("INSERT INTO invites (code, created) VALUES (?,?)", code, now())
        return code

    # ── 会话 token（HMAC） ───────────────────────────────────────────
    def token(self, subject: str, ttl: int = 7 * 86400, *, expires_at=None) -> str:
        exp = int(expires_at) if expires_at is not None else int(time.time()) + ttl
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
            want = hmac.new(self.secret, f"{subject}|{exp}".encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig,want) or int(exp)<time.time():return None
        except (ValueError,TypeError,UnicodeError):
            return None
        return subject


__all__ = ["Auth", "KINDS", "INTERNAL", "USERNAME"]
