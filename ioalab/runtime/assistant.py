"""个人助手：openJiuwen ReActAgent + A2C 委托方 SDK 工具。

一单多轮：轮 1 由模型驱动（发现 → 信誉 → 取模板 → 拟卡 → 签字 → 开工）；之后的取件、验收、付款是
委托方 SDK 的固定流程（签约后零模型调用），由平台在收到存证通知时执行；环节请求与验收亲签在
「仿真代签」关闭时上升给用户确认。

签名类工具里由平台注入亲签开关、连接授权摘要、市场与预算，模型不需要也不能改它们。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from a2c.client import Client

from .llm import LLM, parse_json
from .ops import DelegationOps

log = logging.getLogger("ioalab.assistant")


def _out(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Hooks:
    """助手运行时向平台要的东西（全部同步，可在工作线程调用）。"""

    def __init__(self, *, search: Callable[[str, int], list[dict]], reputation: Callable[[str], dict],
                 wallet: Callable[[], str], confirm: Callable[[str, str, str], bool],
                 arbitrate: Callable[[str, int, str], dict], on_phase: Callable[[str, str, dict], None],
                 market: str, tried: Callable[[str], list[str]] | None = None) -> None:
        self.search, self.reputation, self.wallet, self.confirm = search, reputation, wallet, confirm
        self.arbitrate, self.on_phase, self.market = arbitrate, on_phase, market
        self.tried = tried or (lambda nid: [])


PERSONA = """你是「{name}」，一位个人助手，替用户在智能体交易市场里委托专业服务智能体干活。你遵守 A2C 合同协议的流程：
1. 用 search_services 按语义找候选服务（只信市场返回的被认证服务方）。
2. 从候选里挑 2–3 家最贴合的（status 为 online 的在接单，优先），用 get_reputation 看信誉；用 list_templates 取各家公示的合同模板，
   按「交付件 → 输入要求 → 价 → 验收标准」逐节比较：不满足用户要求的立刻剔除；价不得超过预算上限；验收标准越严越好。
3. 选定后：预批准型 / 待批准型用 draft_card 拟卡（spec 按模板的 specSchema 填，枚举值必须在域内；材料用 list_materials 给的名字），
   再 sign_card；提案型用 submit_request，拿到服务方拟的卡后 adopt。待批准型服务方可能拒绝复签、提案型服务方可能退回提案卡
   （reason 里会说原因，如「已接满」）：被拒的那家**不要再找**，直接换候选列表里的另一家（同类里描述相近即可，如另一家简历、行程服务），
   最多换 3 家：换家时直接取那家的模板拟卡 / 提交提案卡，不必再查信誉、不必重新检索。
4. 卡生效后调 start 开工。开工成功即任务完成。
规则：报价必须是定数；不要编造工具结果；不要替用户决定超预算的事；每一步只调必要的工具，不要反复检索。
最后用一段 JSON 作答：{{"cardDigest": "...", "provider": "...", "price": 数字, "summary": "一句话"}}；失败则 {{"failed": "原因"}}。"""


class Round1Error(RuntimeError):
    """第一轮中途失败：带上已发生的工具轨迹，平台据此与订单表判断是重试还是按订单状态收尾。"""

    def __init__(self, msg: str, trace: list[dict]) -> None:
        super().__init__(msg)
        self.trace = trace


class AssistantRuntime:
    def __init__(self, *, agent_id: str, name: str, client: Client, llm: LLM, hooks: Hooks, home: Path,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.agent_id, self.name, self.client, self.llm, self.hooks, self.home, self.loop = \
            agent_id, name, client, llm, hooks, home, loop
        self.home.mkdir(parents=True, exist_ok=True)
        self._agent = None

    # ── 工具（绑定到一条需求）：DelegationOps 的薄包装，REST 用同一套 ops ─────
    def ops(self, need: Mapping[str, Any]) -> DelegationOps:
        return DelegationOps(self, need)

    def tools(self, need: Mapping[str, Any]) -> list:
        from openjiuwen.core.foundation.tool import tool

        ops = self.ops(need)

        async def call(fn, *a):
            try:
                return _out(await asyncio.to_thread(fn, *a))
            except Exception as e:  # noqa: BLE001
                return _out({"error": str(e)[:400]})

        @tool
        async def search_services(query: str, top_k: int = 8) -> str:
            """在交易中心（Agent 交易市场）按语义检索被认证的服务方。

            Args:
                query: 用一句话描述要委托的事
                top_k: 最多返回几家
            """
            return await call(ops.search, query, top_k)

        @tool
        async def get_reputation(provider: str) -> str:
            """取某服务方在市场的信誉条目（已完成总数 + 成功比例，市场签发）。

            Args:
                provider: 服务方 URL（search_services 返回的 provider）
            """
            return await call(ops.reputation, provider)

        @tool
        async def list_templates(provider: str) -> str:
            """取服务方公示的合同模板（已验签、未过期）：交付件 specSchema、输入要求、截止时间、分期规范、验收标准。

            Args:
                provider: 服务方 URL
            """
            return await call(ops.templates, provider)

        @tool
        async def list_materials() -> str:
            """列出用户为这条需求准备的材料（名字、声明属性、预览），拟卡时按名字引用。"""
            return await call(ops.materials)

        @tool
        async def draft_card(template_digest: str, card_id: str, spec: dict, material_names: list[str] | None = None) -> str:
            """按模板拟合同卡（预批准型、待批准型）。

            Args:
                template_digest: list_templates 返回的 templateDigest
                card_id: 你起的卡号，如 C-001
                spec: {交付件名: {键: 值}}，键与取值须符合模板 specSchema（枚举必须在域内；const 的键不填）
                material_names: 要交给服务方的材料名（list_materials 里的 name），按模板 inputs 的 name 一一对应
            """
            return await call(ops.draft, template_digest, card_id, spec, material_names or [])

        @tool
        async def sign_card(card_digest: str) -> str:
            """签1 采纳合同卡（定约亲签开着时先问用户）；待批准型随即请服务方复签，返回复签结果或拒绝理由。

            Args:
                card_digest: draft_card 返回的 cardDigest
            """
            return await call(ops.sign, card_digest)

        @tool
        async def submit_request(template_digest: str, request_id: str, spec: dict, material_names: list[str] | None = None) -> str:
            """提案型：填提案卡（付拟卡费如收）发给服务方，返回服务方拟的合同卡摘要与报价，或退回理由。

            Args:
                template_digest: 提案型模板的 templateDigest
                request_id: 你起的提案卡号，如 R-001
                spec: {交付件名: {键: 值}}
                material_names: 要交的材料名
            """
            return await call(ops.submit_request, template_digest, request_id, spec, material_names or [])

        @tool
        async def adopt(card_digest: str) -> str:
            """提案型：采纳服务方拟的合同卡（签1，定约亲签开着时先问用户）。

            Args:
                card_digest: submit_request 返回的 cardDigest
            """
            return await call(ops.adopt, card_digest)

        @tool
        async def start(card_digest: str) -> str:
            """开工：付定金（如有）、交材料、发开工请求并订阅市场的存证通知。成功即本轮任务完成。

            Args:
                card_digest: 已生效的合同卡摘要
            """
            return await call(ops.start, card_digest)

        @tool
        async def wallet() -> str:
            """看自己钱包的余量（虚拟币）。"""
            return await call(ops.wallet)

        return [search_services, get_reputation, list_templates, list_materials, draft_card, sign_card, submit_request,
                adopt, start, wallet]

    # ── 轮 1：模型驱动 ───────────────────────────────────────────────
    async def run_round1(self, need: Mapping[str, Any]) -> dict:
        from openjiuwen.core.single_agent import AgentCard, ReActAgent
        agent = ReActAgent(card=AgentCard(id=f"{self.agent_id}:{need['id']}", name=self.name)).configure(
            self.llm.agent_config(PERSONA.format(name=self.name), max_iterations=24))
        for t in self.tools(need):
            agent.ability_manager.add_ability(t.card, t)
        trace: list[dict] = []
        try:
            from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

            async def cb(ctx):
                try:
                    trace.append({"tool": ctx.inputs.tool_name, "args": str(ctx.inputs.tool_args)[:300],
                                  "result": str(ctx.inputs.tool_result)[:300], "at": time.time()})
                except Exception:  # noqa: BLE001
                    pass
            await agent.register_callback(AgentCallbackEvent.AFTER_TOOL_CALL, cb)
        except Exception as e:  # noqa: BLE001
            log.debug("no tool callback: %s", e)
        scope = need["scope"]
        query = (f"用户需求：{need['text']}\n预算上限：{scope.get('budget')} 虚拟币；可委托类别：{scope.get('category', '不限')}；"
                 f"定约亲签：{'是' if scope.get('sign1') == 'user' else '否'}；验收亲签：{'是' if scope.get('sign7') == 'user' else '否'}。\n"
                 f"可用材料：{[m['name'] for m in need.get('materials', [])]}。请完成到开工。")
        if need.get("resume"):                            # 上一轮在签卡之后断了：卡已生效，直接开工，不要再检索、拟卡
            query += (f"\n注意：此前已拟好并签了合同卡 {need['resume']['cardDigest']}（服务方 {need['resume']['provider']}），"
                      "合同已生效；不要重新检索或拟卡，直接用这个 card_digest 调用 start 开工。")
        try:
            async with self.llm.gate.asem():
                res = await agent.invoke({"query": query, "conversation_id": need["id"]})
        except Exception as e:  # noqa: BLE001
            # 模型在收尾那一问才连不上：此前的工具调用（可能已开工）与用量都得留下，交给平台按订单状态定结局
            self._record_usage(agent, need["id"])
            raise Round1Error(str(e), trace) from e
        self._record_usage(agent, need["id"])
        out = res.get("output", "") if isinstance(res, dict) else str(res)
        try:
            parsed = parse_json(out)
        except Exception:  # noqa: BLE001
            parsed = {"failed": "助手没有给出结构化结果", "raw": out[:500]}
        return {"result": parsed, "result_type": res.get("result_type") if isinstance(res, dict) else None, "trace": trace}

    def _record_usage(self, agent, key: str) -> None:
        tokens, calls = 0, 0
        try:                                              # 用量：上下文里每条 assistant 消息带 usage_metadata
            for m in agent.context_engine.get_context(session_id=key).get_messages():
                u = getattr(m, "usage_metadata", None)
                if u is not None and getattr(m, "role", "") in ("assistant", "ai"):
                    calls += 1
                    tokens += int(getattr(u, "total_tokens", 0) or (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "output_tokens", 0) or 0))
        except Exception as e:  # noqa: BLE001
            log.debug("usage not available: %s", e)
        self.llm.usage.add(key, calls, tokens)

    # ── 之后：委托方 SDK 的固定流程（零模型调用）────────────────────
    def handle_anchored(self, need: Mapping[str, Any], cd: str, period: int) -> dict:
        """取件 → 验收器 → 通过则签7 付款（验收亲签时先问用户）；不通过则申请仲裁（关闭代签时先问用户）。"""
        auto = bool(need.get("auto_sign", 1))
        scope = need["scope"]
        files = self.client.fetch_delivery(cd, period)
        target = self.home / "deliveries" / cd.split(":")[1][:16] / str(period)
        target.mkdir(parents=True, exist_ok=True)
        for n, b in files.items():
            (target / Path(n).name).write_bytes(b)
        self.hooks.on_phase(need["id"], "fetched", {"cardDigest": cd, "period": period, "files": list(files)})
        result = self.client.verify(cd, period)
        summary = {"status": result.status, "results": [{"verifier": r.ref["id"], "status": r.outcome.status,
                                                         "checks": r.outcome.checks} for r in result.runs],
                   "errors": result.errors}
        self.hooks.on_phase(need["id"], "verified", {"cardDigest": cd, "period": period, **summary})
        if result.status == "pass":
            if scope.get("sign7") == "user" and not auto:
                ok = self.hooks.confirm(need["id"], "accept", f"第 {period} 期验收：{len(summary['results'])} 条判据全部通过，确认后付本期款")
                if not ok:
                    return {"status": "pass", "accepted": False}
            acc = self.client.accept(cd, period)
            self.hooks.on_phase(need["id"], "accepted", {"cardDigest": cd, "period": period,
                                                          "paid": acc.voucher["amount"] if acc.voucher else None})
            return {"status": "pass", "accepted": True, "paid": acc.voucher["amount"] if acc.voucher else None}
        if result.status == "pending":
            return {"status": "pending", "notBefore": result.not_before}
        acc = self.client.accept(cd, period)                 # 签不通过的验收报告并通知服务方
        if not auto:
            ok = self.hooks.confirm(need["id"], "arbitrate", f"第 {period} 期验收未通过：{summary}。是否申请仲裁？")
            if not ok:
                return {"status": "fail", "arbitrated": False}
        ruling = self.hooks.arbitrate(cd, period, "acceptance-failed")
        self.hooks.on_phase(need["id"], "arbitrated", {"cardDigest": cd, "period": period, "liable": ruling.get("liable")})
        return {"status": "fail", "arbitrated": True, "ruling": ruling}

    def handle_step(self, need: Mapping[str, Any], cd: str, step_request: Mapping[str, Any]) -> bool:
        auto = bool(need.get("auto_sign", 1))
        granted = True
        if not auto:
            granted = self.hooks.confirm(need["id"], "step", f"服务方请求以你的名义：{step_request.get('step')}。是否授权？")
        self.client.answer_step(cd, granted)
        self.hooks.on_phase(need["id"], "step-answered", {"cardDigest": cd, "step": step_request.get("step"), "granted": granted})
        return granted


__all__ = ["AssistantRuntime", "Hooks", "PERSONA"]
