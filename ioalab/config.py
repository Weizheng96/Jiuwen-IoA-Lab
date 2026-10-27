"""运行配置：全部来自环境变量（IOALAB_*），有默认值；凭据不读任何文件。

部署者只配 deploy/ioalab.env（见 deploy/ioalab.env.example）；A2X 要的 llm_apikey.json 由平台按同一把 key 生成到 data/a2x_home/。
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from . import ROOT

DEFAULT_LLM_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _stub() -> bool:
    """IOALAB_STUB=1：全程不调模型——交付件走定义里的 sample，发现走字符二元组退路，不读凭据。"""
    return _env("IOALAB_STUB") == "1"


def _llm() -> dict:
    """OpenAI 兼容端点：IOALAB_LLM_API_KEY + IOALAB_LLM_BASE_URL。缺 key 或桩模式 → 空，平台以「无模型」模式起（只能跑桩）。"""
    if _stub():
        return {}
    key = _env("IOALAB_LLM_API_KEY")
    if not key:
        return {}
    return {"api_key": key, "api_base": _env("IOALAB_LLM_BASE_URL", DEFAULT_LLM_BASE).rstrip("/"),
            "fallback_base": _env("IOALAB_LLM_BASE_URL_FALLBACK").rstrip("/")}


@dataclass
class Settings:
    """全部字段在实例化时读环境变量（不是类定义时），所以测试可先设环境再 Settings()。"""
    mode: str = field(default_factory=lambda: _env("IOALAB_MODE", "dev"))                 # dev · trial
    bind: str = field(default_factory=lambda: _env("IOALAB_BIND", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("IOALAB_PORT", "8750")))
    public_url_env: str = field(default_factory=lambda: _env("IOALAB_PUBLIC_URL").rstrip("/"))
    data: Path = field(default_factory=lambda: Path(_env("IOALAB_DATA", str(ROOT / "data"))))
    stub: bool = field(default_factory=_stub)
    llm: dict = field(default_factory=_llm)
    agent_model: str = field(default_factory=lambda: _env("IOALAB_LLM_MODEL", "qwen3.7-flash"))       # 助手与服务方
    a2x_model: str = field(default_factory=lambda: _env("IOALAB_A2X_MODEL", "qwen-flash"))             # A2X 分类树检索
    embedding_model: str = field(default_factory=lambda: _env("IOALAB_EMBED_MODEL", "text-embedding-v4"))
    llm_concurrency: int = field(default_factory=lambda: int(_env("IOALAB_LLM_CONCURRENCY", "4")))
    round1_attempts: int = field(default_factory=lambda: int(_env("IOALAB_ROUND1_ATTEMPTS", "5")))     # 第一轮遇模型连接类错误最多试几次
    registration: str = field(default_factory=lambda: _env("IOALAB_REGISTRATION", "open"))              # open · invite · closed
    login_credit: str = field(default_factory=lambda: _env("IOALAB_LOGIN_CREDIT", "100"))              # 每次成功登录赠送的虚拟币
    isc_count: int = field(default_factory=lambda: int(_env("IOALAB_ISC_COUNT", "10")))                # 内部服务使用者账号数 M
    traffic: str = field(default_factory=lambda: _env("IOALAB_TRAFFIC", "off"))                        # off · low · normal
    autoseed: bool = field(default_factory=lambda: _env("IOALAB_AUTOSEED", "1") == "1")                # 首次启动自动注入内部账号与服务
    quota_needs_per_day: int = field(default_factory=lambda: int(_env("IOALAB_NEEDS_PER_DAY", "0")))   # 0 → 默认 20
    admin_password: str = field(default_factory=lambda: _env("IOALAB_ADMIN_PASSWORD"))
    message_retention_days: int = field(default_factory=lambda:int(_env('IOALAB_MESSAGE_RETENTION_DAYS','30')))
    message_metadata_days: int = field(default_factory=lambda:int(_env('IOALAB_MESSAGE_METADATA_DAYS','90')))
    message_pending_limit: int = field(default_factory=lambda:int(_env('IOALAB_MESSAGE_PENDING_LIMIT','1000')))
    message_rate: int = field(default_factory=lambda:int(_env('IOALAB_MESSAGE_RATE','60')))
    message_ttl_days: int = field(default_factory=lambda:int(_env('IOALAB_MESSAGE_TTL_DAYS','7')))
    assistant_password: str = field(default_factory=lambda:_env('IOALAB_ASSISTANT_PASSWORD'))
    sandbox_concurrency: int = 2
    sandbox_fuel: int = 5_000_000_000
    currency: str = "CNY"                  # 协议内币种（SDK 白名单）；页面显示为「虚拟币」

    # ── 地址 ─────────────────────────────────────────────────────────
    @property
    def public_url(self) -> str:
        """对外地址：参与方 id（市场 · 支付方 · 服务方 URL）、模板与 A2X 索引都内嵌它。首次 init 后不可改（Platform 校验）。"""
        host = self.bind if self.bind not in ("0.0.0.0", "::", "") else "127.0.0.1"
        return self.public_url_env or f"http://{host}:{self.port}"

    @property
    def base_url(self) -> str:
        return self.public_url

    @property
    def local_url(self) -> str:
        """进程内各参与方互相调用走回环（Nginx 后面不绕圈）。"""
        return f"http://127.0.0.1:{self.port}"

    @property
    def welcome_credit(self) -> str:
        return self.login_credit

    @property
    def a2x_home(self) -> Path:
        return self.data / "a2x_home"

    # ── 模式 ─────────────────────────────────────────────────────────
    @property
    def open(self) -> bool:
        """trial：对外提供（限速、关 /api/docs、密码规则）；dev：本机开发。"""
        return self.mode != "dev"

    @property
    def needs_per_day(self) -> int:
        return self.quota_needs_per_day or 20

    def dirs(self) -> None:
        for d in ("keys", "packages", "a2x", "a2x_home", "deliveries", "runs", "needs", "logs"):
            (self.data / d).mkdir(parents=True, exist_ok=True)

    def secret(self) -> bytes:
        """会话签名密钥：IOALAB_SECRET 或 data/secret（首次生成，0600）。"""
        env = _env("IOALAB_SECRET")
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

    def check(self) -> list[str]:
        """部署前自检：返回问题清单（空 = 可起）。"""
        problems = []
        if self.mode not in ("dev", "trial"):
            problems.append(f"IOALAB_MODE 只能是 dev 或 trial：{self.mode!r}")
        if self.registration not in ("open", "invite", "closed"):
            problems.append(f"IOALAB_REGISTRATION 只能是 open / invite / closed：{self.registration!r}")
        if self.traffic != "off":
            problems.append(f"背景流量须通过 experiments/traffic.py 独立运行；IOALAB_TRAFFIC 只能是 off：{self.traffic!r}")
        if self.open and not self.stub:
            if len(self.admin_password) < 12:
                problems.append("trial 模式必须设置 IOALAB_ADMIN_PASSWORD（≥ 12 位）")
            if not self.public_url_env:
                problems.append("trial 模式必须设置 IOALAB_PUBLIC_URL（参与方 id 内嵌它，首次 init 后不可改）")
        if not 1<=self.message_ttl_days<=self.message_retention_days<=self.message_metadata_days:
            problems.append('消息有效期、正文保留期、元数据保留期必须正数且依次不减')
        if min(self.message_rate,self.message_pending_limit)<1:
            problems.append('消息速率与容量必须大于0')
        return problems


def _chmod600(p: Path) -> None:
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
