# -*- coding: utf-8 -*-
"""DelegationOps 的鲁棒性：模板按服务方 URL / 摘要前缀解析（模型抄错摘要）；规格清洗（const 键 · 未知键 · 数值字符串 · 交付件名写错）；
第一轮没签出卡就作答时平台重跑一次（桩模式下用假的 run_round1 验证）。"""
from __future__ import annotations

import asyncio

import pytest

from a2c.errors import A2CError

from _support import insert_need, new_assistant, new_service
from ioalab.runtime.ops import DelegationOps


def test_clean_spec():
    t = {"deliverables": [{"name": "posts", "specSchema": {"shop": {"type": "string"}, "count": {"const": 3}, "n": {"type": "integer"}, "p": {"type": "number"}}}]}
    spec, notes = DelegationOps.clean_spec(t, {"posts": {"shop": "小满", "count": 3, "n": "7", "p": "1.5", "extra": 1}})
    assert spec == {"posts": {"shop": "小满", "n": 7, "p": 1.5}}
    assert any("const" in x for x in notes) and any("extra" in x for x in notes)
    # 交付件名写错且只有一个交付件 → 归到它；缺的交付件补空
    spec, notes = DelegationOps.clean_spec(t, {"post": {"shop": "x"}})
    assert spec == {"posts": {"shop": "x"}} and any("唯一交付件" in x for x in notes)
    assert DelegationOps.clean_spec(t, {})[0] == {"posts": {}}


def test_template_resolution_and_draft(platform):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)
    u, a, rt = new_assistant(pf)
    need = insert_need(pf, u["id"], a["id"], "三条文案", state="open")
    ops = DelegationOps(rt, need)
    [t] = ops.templates(provider)
    full = t["templateDigest"]
    # 服务方 URL · 完整摘要 · 前缀 · 抄错尾部（前 16 位对）都能解析；短前缀与不存在的报可读错误
    assert ops.template(provider)["templateDigest"] == full
    assert ops.template(full)["templateDigest"] == full
    assert ops.template(full[:7 + 24])["templateDigest"] == full
    assert ops.template(full[:7 + 40] + "deadbeefdeadbeefdeadbeef")["templateDigest"] == full
    with pytest.raises(A2CError, match="原样复制"):
        ops.template("sha256:" + "0" * 64)
    # 拟卡：填了 const 键与未知键也能拟出来，notices 里说明
    out = ops.draft(provider, "C-OPS", {"posts": {"shop": "小满美甲", "theme": "春", "count": 3, "bogus": "x"}})
    assert out["cardDigest"].startswith("sha256:") and str(out["derived"]["total"]) in ("6", "6.0", "6.00")
    assert any("const" in n for n in out["notices"]) and any("bogus" in n for n in out["notices"])
    # 预批准型模板发提案卡 → 可读错误
    with pytest.raises(A2CError, match="提案型"):
        ops.submit_request(provider, "R-1", {"posts": {"shop": "x"}})


def test_round1_answer_without_card_is_rerun(platform, monkeypatch):
    """模型只检索就作答「已委托」：平台带提示重跑一次；第二次仍无卡则 failed。"""
    pf = platform
    u, a, rt = new_assistant(pf)
    calls = []

    async def fake_round1(need):
        calls.append(need.get("retry_hint"))
        return {"result": {"provider": "x", "summary": "已选定服务方，稍后拟卡"}, "result_type": None, "trace": [{"tool": "search_services"}]}
    monkeypatch.setattr(rt, "run_round1", fake_round1)
    pf.llm.enabled = True                         # 让 create_need 走 _run_need（桩模式下 LLM 关着时也会排任务）
    try:
        need = pf.create_need(u["id"], a["id"], "随便做点什么", {"budget": 10}, by="admin")
        for _ in range(100):
            st = pf.need(need["id"])["state"]
            if st in ("failed", "completed", "started"):
                break
            asyncio.run(asyncio.sleep(0.2))
    finally:
        pf.llm.enabled = False
    assert pf.need(need["id"])["state"] == "failed"
    assert len(calls) == 2 and calls[0] is None and "必须实际调用工具" in calls[1]
    evs = pf.db.rows("SELECT op, detail FROM events WHERE outcome=? AND op='need.retry'", need["id"])
    assert evs and "没有完成到开工就作答" in evs[0]["detail"]
