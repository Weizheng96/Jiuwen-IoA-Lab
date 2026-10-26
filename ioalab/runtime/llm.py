"""模型：openJiuwen ReActAgent 的配置工厂、全局并发闸、用量统计，以及服务方生成交付件用的 JSON 调用。

所有模型调用（智能体 + A2X 分类树检索）共用一个 ``GATE``；``import openjiuwen`` 前先把日志收到 console。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import weakref
from typing import Any, Mapping

from ..config import Settings

log = logging.getLogger("ioalab.llm")
os.environ.setdefault("OPENJIUWEN_LOG_OUTPUT", "console")

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)


def strip_fence(text: str) -> str:
    return _FENCE.sub("", text.strip())


def parse_json(text: str) -> Any:
    t = strip_fence(text)
    try:
        return json.loads(t)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        i, j = t.find("["), t.rfind("]")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


class Gate:
    """进程级并发闸：同步线程用 ``sem``，协程用 ``asem``（二者独立计数，各自限到同一上限）。"""

    def __init__(self, n: int) -> None:
        self.n = n
        self.sem = threading.Semaphore(n)
        self._asems: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()

    def asem(self) -> asyncio.Semaphore:
        # asyncio.Semaphore 绑定首次使用它的事件循环；助手每条需求各有自己的循环、服务方在 uvicorn 的循环，
        # 共用一个会报「bound to a different event loop」——按循环各建一个（每个循环各限到 n）
        loop = asyncio.get_running_loop()
        sem = self._asems.get(loop)
        if sem is None:
            sem = self._asems[loop] = asyncio.Semaphore(self.n)
        return sem


class Usage:
    """按 conversation_id（= 需求 id）累计调用次数与 token。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_key: dict[str, dict] = {}

    def add(self, key: str, calls: int = 1, tokens: int = 0) -> None:
        with self.lock:
            d = self.by_key.setdefault(key, {"calls": 0, "tokens": 0})
            d["calls"] += calls
            d["tokens"] += tokens

    def get(self, key: str) -> dict:
        with self.lock:
            return dict(self.by_key.get(key, {"calls": 0, "tokens": 0}))


class LLM:
    FAILOVER_AFTER = 3          # 两分钟内连续几次连接类失败就切端点
    FAILOVER_WINDOW = 120.0

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.gate = Gate(settings.llm_concurrency)
        self.usage = Usage()
        self.enabled = bool(settings.llm.get("api_key"))
        self.bases = [b for b in (settings.llm.get("api_base"), settings.llm.get("fallback_base")) if b]
        self.active = 0
        self._fail_at: list[float] = []
        self._flock = threading.Lock()
        self.on_failover = None                     # 平台挂钩：切端点时发事件
        self.failovers = 0
        if self.enabled:
            # httpx 在 Windows 上会读注册表里的系统代理（如 127.0.0.1:7890），模型请求经它中转会间歇性挂住到超时；
            # 把模型主机与回环地址加进 NO_PROXY，直连
            for var in ("NO_PROXY", "no_proxy"):
                cur = [x for x in os.environ.get(var, "").split(",") if x]
                for h in [b.split("//", 1)[-1].split("/", 1)[0] for b in self.bases] + ["127.0.0.1", "localhost"]:
                    if h not in cur:
                        cur.append(h)
                os.environ[var] = ",".join(cur)

    @property
    def api_base(self) -> str:
        return self.bases[self.active] if self.bases else ""

    def note_failure(self, reason: str = "") -> bool:
        """连接类失败记一次；两分钟内满 FAILOVER_AFTER 次且配了备用端点就切换。返回是否切了。"""
        with self._flock:
            now_ = time.time()
            self._fail_at = [t for t in self._fail_at if now_ - t < self.FAILOVER_WINDOW] + [now_]
            if len(self.bases) < 2 or len(self._fail_at) < self.FAILOVER_AFTER:
                return False
            self._fail_at.clear()
            self.active = (self.active + 1) % len(self.bases)
            self.failovers += 1
        log.warning("LLM endpoint failover → %s (%s)", self.api_base, reason[:120])
        if self.on_failover:
            try:
                self.on_failover(self.api_base, reason)
            except Exception as e:  # noqa: BLE001
                log.debug("failover hook failed: %s", e)
        return True

    def note_success(self) -> None:
        with self._flock:
            self._fail_at.clear()

    # ── openJiuwen ────────────────────────────────────────────────────
    def agent_config(self, persona: str, *, max_iterations: int = 12, temperature: float = 0.2):
        from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
        from openjiuwen.core.single_agent import ReActAgentConfig
        if not self.enabled:
            raise RuntimeError("未配置模型凭据（APIkey/*apiKey*.csv）")
        # OpenAI 兼容端点 + 关思考（冒烟：关思考 1.7–3.6 s，开思考 4–18 s 且带工具时可能超时）；不共用进程级 HTTP 客户端
        client = ModelClientConfig(client_provider="OpenAI", api_base=self.api_base, api_key=self.s.llm["api_key"],
                                   timeout=120, max_retries=2, use_shared_llm_http_client=False)
        req = ModelRequestConfig(model=self.s.agent_model, temperature=temperature, reasoning={"mode": "disabled"})
        return ReActAgentConfig(model_client_config=client, model_config_obj=req,
                                prompt_template=[{"role": "system", "content": persona}], max_iterations=max_iterations)

    def agent(self, agent_id: str, persona: str, *, max_iterations: int = 12):
        from openjiuwen.core.single_agent import AgentCard, ReActAgent
        return ReActAgent(card=AgentCard(id=agent_id, name=agent_id)).configure(
            self.agent_config(persona, max_iterations=max_iterations))

    # ── 嵌入（服务发现的向量退路）────────────────────────────────────
    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        out: list[list[float]] = []
        for i in range(0, len(texts), 10):
            batch = texts[i:i + 10]
            for attempt in range(3):                      # 网络抖动：退避重试，仍不通就抛给调用方走退路
                try:
                    with self.gate.sem:
                        r = httpx.post(self.api_base + "/embeddings", json={"model": self.s.embedding_model, "input": batch},
                                       headers={"Authorization": "Bearer " + self.s.llm["api_key"]}, timeout=60, trust_env=False)
                    self.note_success()
                    break
                except httpx.HTTPError as e:
                    self.note_failure(f"embed: {e}")
                    if attempt == 2:
                        raise
                    time.sleep(5 * (attempt + 1))
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return out

    # ── 直接 JSON 调用（服务方生成交付件、提案报价）───────────────────
    async def chat_json(self, system: str, user: str, *, key: str = "", max_tokens: int = 4096,
                        temperature: float = 0.3, retries: int = 4) -> Any:
        """OpenAI 兼容非流式 chat.completions，关思考，返回解析后的 JSON；失败重试。"""
        import httpx
        if not self.enabled:
            raise RuntimeError("未配置模型凭据")
        body = {"model": self.s.agent_model, "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": user}],
                "stream": False, "max_tokens": max_tokens, "temperature": temperature, "enable_thinking": False,
                "response_format": {"type": "json_object"}}
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with self.gate.asem():
                    async with httpx.AsyncClient(timeout=180) as http:
                        r = await http.post(self.api_base + "/chat/completions", json=body,
                                            headers={"Authorization": "Bearer " + self.s.llm["api_key"]})
                self.note_success()
            except httpx.HTTPError as e:              # 网络抖动：连接 / 读超时，退避后重试
                last = e
                self.note_failure(f"chat: {e}")
                await asyncio.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                last = RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:200]}")
                continue
            d = r.json()
            self.usage.add(key, 1, int((d.get("usage") or {}).get("total_tokens") or 0))
            content = d["choices"][0]["message"].get("content") or ""
            try:
                return parse_json(content)
            except ValueError as e:
                last = e
                body["messages"].append({"role": "assistant", "content": content})
                body["messages"].append({"role": "user", "content": "只输出合法 JSON，不要任何其他文字。"})
        raise RuntimeError(f"模型未返回合法 JSON：{last}")


__all__ = ["LLM", "Gate", "Usage", "parse_json", "strip_fence"]
