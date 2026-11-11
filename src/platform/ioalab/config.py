"""Typed platform settings; runtime values come from one explicitly selected JSON file."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LLM_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass
class Settings:
    """Explicit settings objects support isolated component construction; files are read only by PlatformConfiguration."""
    mode: str = "dev"
    bind: str = "127.0.0.1"
    port: int = 8750
    public_url_env: str = ""
    trial_url: str = ""
    trial_confirm_url: str = ""
    trial_admission_key: str = field(default="",repr=False)
    data: Path = field(default_factory=lambda: Path("data").resolve())
    seed_file: Path | None = None
    bundles_dir: Path = field(default_factory=lambda: Path("dist").resolve())
    connection_ticket_seconds: int = 60
    ws_heartbeat_seconds: int = 15
    ws_lease_seconds: int = 45
    stub: bool = False
    llm: dict = field(default_factory=dict,repr=False)
    agent_model: str = "qwen3.7-flash"
    a2x_model: str = "qwen-flash"
    embedding_model: str = "text-embedding-v4"
    llm_concurrency: int = 4
    round1_attempts: int = 5
    registration: str = "open"
    login_credit: str = "100"
    isc_count: int = 10
    traffic: str = "off"
    autoseed: bool = True
    quota_needs_per_day: int = 0
    admin_password: str = field(default="admin1234",repr=False)
    message_retention_days: int = 30
    message_metadata_days: int = 90
    message_pending_limit: int = 1000
    message_rate: int = 60
    message_ttl_days: int = 7
    assistant_password: str = field(default="",repr=False)
    arbitration_models: dict = field(default_factory=dict)
    sandbox_concurrency: int = 2
    sandbox_fuel: int = 5_000_000_000
    currency: str = "CNY"                  # 协议内币种（SDK 白名单）；页面显示为「虚拟币」

    admin_email: str = "zhengw.cc@outlook.com"
    admin_username: str = "admin"
    session_secret: str = field(default="",repr=False)
    initial_credit: str = "500"
    repeat_daily_credit: bool = False
    auth_max_failures: int = 40
    auth_window_seconds: int = 60
    registration_limit: int = 5
    registration_window_seconds: int = 3600
    local_unlimited: bool = True
    local_addresses: tuple = ("127.0.0.1","::1")
    trusted_proxy_addresses: tuple = ()
    policy_source: object = field(default=None,repr=False,compare=False)

    def policy(self):
        """Current hot-editable policy: from the injected source, else these field values."""
        if self.policy_source is not None:return self.policy_source()
        return {"authentication":{"max_failures":self.auth_max_failures,"window_seconds":self.auth_window_seconds,"registration_limit":self.registration_limit,"registration_window_seconds":self.registration_window_seconds,"local_unlimited":self.local_unlimited},"credits":{"initial":self.initial_credit,"login":self.login_credit,"repeat_daily":self.repeat_daily_credit,"day_timezone":"Asia/Shanghai"}}

    # ── 地址 ─────────────────────────────────────────────────────────
    @property
    def public_url(self) -> str:
        """对外地址：参与方 id（市场 · 支付方 · 服务方 URL）、模板与 A2X 索引都内嵌它。首次 init 后不可改（Platform 校验）。"""
        return self.public_url_env or self.local_url

    @property
    def base_url(self) -> str:
        return self.public_url

    @property
    def local_url(self) -> str:
        """内部调用使用实际监听地址；通配监听转换为同地址族回环。"""
        host = {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(self.bind, self.bind)
        authority = f"[{host}]" if ":" in host else host
        return f"http://{authority}:{self.port}"

    @property
    def a2x_home(self) -> Path:
        return self.data / "a2x_home"

    # ── 模式 ─────────────────────────────────────────────────────────
    @property
    def open(self) -> bool:
        """trial：对外提供（限速、关 /api/docs、密码规则）；dev：本机开发。"""
        return self.mode != "dev"

    def dirs(self) -> None:
        for d in ("keys", "packages", "a2x", "a2x_home", "deliveries", "runs", "logs"):
            (self.data / d).mkdir(parents=True, exist_ok=True)

    def secret(self) -> bytes:
        """显式会话签名密钥或原data/secret；首次生成后保留。"""
        env = self.session_secret
        if env:
            return env.encode()
        p = self.data / "secret"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(secrets.token_hex(32), encoding="utf-8")
            _chmod600(p)
        return p.read_text(encoding="utf-8").strip().encode()

    # ── A2X 凭据文件（与平台共用一把 key）────────────────────────────
    def write_a2x_key(self) -> Path:
        """按 A2X LLMClient 的格式写 data/a2x_home/llm_apikey.json：providers[{name, base_url(完整 chat/completions), model, api_keys}]。"""
        self.a2x_home.mkdir(parents=True, exist_ok=True)
        p = self.a2x_home / "llm_apikey.json"
        providers = []
        if self.llm.get("api_key"):
            providers.append({"name": "platform", "base_url": self.llm["api_base"] + "/chat/completions",
                              "model": self.a2x_model, "api_keys": [self.llm["api_key"]]})
        p.write_text(json.dumps({"providers": providers}, ensure_ascii=False, indent=1), encoding="utf-8")
        _chmod600(p)
        return p

    def a2x_configured(self) -> bool:
        return bool(self.llm.get("api_key")) and not self.stub

    def check(self, *, seed_manifest) -> list[str]:
        """部署前自检：返回问题清单（空 = 可起）。seed_manifest 由装配层传入的种子清单校验函数，无效时抛 ValueError。"""
        problems = []
        if self.mode not in ("dev", "trial"):
            problems.append("runtime.mode 只能是 dev 或 trial")
        if self.registration not in ("open", "invite", "closed"):
            problems.append("runtime.registration 只能是 open / invite / closed")
        if self.traffic != "off":
            problems.append("平台核心不运行参与方，runtime.traffic 只能是 off")
        if self.autoseed:
            try:
                seed_manifest(self.seed_file)
            except ValueError as exc:
                problems.append(str(exc))
        if self.open and not self.stub:
            if not self.admin_username or not self.admin_password:
                problems.append("Configure the deployment administrator username and password")
            if not self.public_url_env:
                problems.append("trial 模式必须设置 runtime.public_url（首次 init 后不可改）")
        if not 1<=self.message_ttl_days<=self.message_retention_days<=self.message_metadata_days:
            problems.append('消息有效期、正文保留期、元数据保留期必须正数且依次不减')
        if min(self.message_rate,self.message_pending_limit)<1:
            problems.append('消息速率与容量必须大于0')
        if not 1<=self.connection_ticket_seconds<=300 or self.ws_heartbeat_seconds<1 or self.ws_lease_seconds<3*self.ws_heartbeat_seconds:
            problems.append('Invalid connection ticket, heartbeat or execution lease durations')
        return problems


def _chmod600(p: Path) -> None:
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
