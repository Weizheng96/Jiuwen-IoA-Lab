"""内部账号：ISP_001…ISP_{N}（内部服务提供者，一个账号可覆盖多个领域）与 ISC_001…ISC_{M}（内部服务使用者）。

密码 = 序号三位（001…）。N 来自显式部署种子清单；M = runtime.isc_count。
幂等：按用户名判已存在。ISC 账号保持空闲，每次成功登录按普通账号规则获得额度。
"""
from __future__ import annotations

def ensure_accounts(platform, *, domains=None) -> dict:
    """建齐内部账号，返回 {"isp": [uid…], "isc": [uid…]}（按序号排列）。"""
    out: dict[str, list[str]] = {"isp": [], "isc": []}
    if domains is None:
        from .domains import load_domains
        domains = load_domains(platform.s.seed_file)
    accounts = dict.fromkeys(domain['account'] for domain in domains)
    for username in accounts:
        out["isp"].append(_ensure(platform, username, username[-3:], "isp"))
    for i in range(1, max(0, platform.s.isc_count) + 1):
        password=platform.s.assistant_password if i==1 and platform.s.assistant_password else f"{i:03d}"
        if i==1 and platform.s.open:
            import secrets
            if password=='001':
                path=platform.s.data/'assistant.secret'
                if not path.exists(): path.write_text(secrets.token_urlsafe(18),encoding='utf-8')
                password=path.read_text(encoding='utf-8').strip()
            if len(password)<12: raise ValueError('ISC_001 requires a password of at least 12 characters in trial mode')
            with platform.db.tx() as conn:
                platform.auth.reset_default_password_in(conn,'ISC_001','001',password)
        uid = _ensure(platform, f"ISC_{i:03d}", password, "isc")
        if i==1:
            with platform.db.tx() as conn:
                platform.auth.grant_admin_in(conn,uid)
        out["isc"].append(uid)
    return out


def _ensure(platform, username: str, password: str, kind: str) -> str:
    row = platform.identities.account(username)
    if row:
        with platform.db.tx() as conn:
            platform.auth.normalize_preset_display_name_in(conn, username, kind)
        return row["id"]
    return platform.accounts.create_account(username, password, kind=kind, display_name=username, note="部署自带")["id"]


__all__ = ["ensure_accounts"]
