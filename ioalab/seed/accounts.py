"""内部账号：ISP_001…ISP_{N}（内部服务提供者，各负责一个领域）与 ISC_001…ISC_{M}（内部服务使用者）。

密码 = 序号三位（001…）。N = seed/domains/ 的领域文件数（没有领域文件时取 DEFAULT_ISP）；M = IOALAB_ISC_COUNT。
幂等：按用户名判已存在。ISC 账号保持空闲，登录后按普通账号规则获得当日额度。
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "official-services"
DEFAULT_ISP = 8


def isp_labels() -> list[str]:
    """各 ISP 的显示名：有领域文件则用领域名（P2a 起），否则「内部服务方 00k」。"""
    files = sorted((HERE / "domains").glob("*.json")) if (HERE / "domains").exists() else []
    if files:
        import json
        return [json.loads(p.read_text(encoding="utf-8")).get("label") or p.stem for p in files]
    return [f"内部服务方 {i:03d}" for i in range(1, DEFAULT_ISP + 1)]


def ensure_accounts(platform) -> dict:
    """建齐内部账号，返回 {"isp": [uid…], "isc": [uid…]}（按序号排列）。"""
    out: dict[str, list[str]] = {"isp": [], "isc": []}
    for i, label in enumerate(isp_labels(), 1):
        out["isp"].append(_ensure(platform, f"ISP_{i:03d}", f"{i:03d}", "isp", f"内部服务方 · {label}"))
    for i in range(1, max(0, platform.s.isc_count) + 1):
        password=platform.s.assistant_password if i==1 and platform.s.assistant_password else f"{i:03d}"
        if i==1 and platform.s.open:
            from ..psp.auth import _check_pw,_pbkdf2
            import secrets
            if password=='001':
                path=platform.s.data/'assistant.secret'
                if not path.exists(): path.write_text(secrets.token_urlsafe(18),encoding='utf-8')
                password=path.read_text(encoding='utf-8').strip()
            if len(password)<12: raise ValueError('ISC_001 requires a password of at least 12 characters in trial mode')
            existing=platform.db.one("SELECT id,pw_hash FROM users WHERE username='ISC_001'")
            if existing and _check_pw('001',existing['pw_hash']):
                platform.db.run('UPDATE users SET pw_hash=? WHERE id=?',_pbkdf2(password),existing['id'])
        uid = _ensure(platform, f"ISC_{i:03d}", password, "isc", f"内部用户 {i:03d}")
        if i==1:
            platform.db.run("INSERT OR IGNORE INTO user_roles VALUES (?, 'admin')",uid)
        out["isc"].append(uid)
    return out


def _ensure(platform, username: str, password: str, kind: str, display_name: str) -> str:
    row = platform.db.one("SELECT id FROM users WHERE username=?", username)
    if row:
        return row["id"]
    return platform.register_user(username, password, kind=kind, display_name=display_name, note="部署自带")["id"]


__all__ = ["ensure_accounts", "isp_labels", "DEFAULT_ISP"]
