# -*- coding: utf-8 -*-
"""用例共用的驱动：建虚拟账号 / 助手 / 服务（每个用例各用自己的 id，互不影响），用委托方 SDK 跑一张卡。"""
from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

from a2c.card import card_digest

from ioalab.seed import load_definitions
from ioalab.store import jdump, now

SCOPE_DIGEST = "sha256:" + "a" * 64
SIGNERS = {"slot1": "client", "slot7": "client"}
COOKIE = "ioalab_session"


def tag() -> str:
    return uuid.uuid4().hex[:6]


def phone() -> str:
    return "13" + str(uuid.uuid4().int)[:9]


def definition(base: str = "wechat-posts") -> dict:
    return next(x for x in load_definitions() if x["id"] == base)


def new_user(pf, note: str = "测试用户") -> dict:
    return pf.register_user(phone(), is_virtual=True, note=note)


def new_assistant(pf, name: str = "小艺"):
    """返回 (用户, 助手行, AssistantRuntime)；注册即送 100。"""
    u = new_user(pf, "测试委托方")
    a = pf.register_assistant(u["id"], name)
    return u, a, pf.assistants[a["id"]]


def new_service(pf, base: str = "wechat-posts", **overrides: Any):
    """按种子定义注册一个独立的服务（id / name 带随机后缀）；返回 (用户, 智能体行, 服务方 URL, 定义)。"""
    t = tag()
    d = {**definition(base), **overrides}
    d["id"] = f"{base}-{t}"
    d["name"] = f"{d['name']}·{t}"
    u = new_user(pf, "测试服务方")
    row = pf.register_service(u["id"], d)
    return u, row, pf.provider_url(row["id"]), d


def sign_and_start(rt, provider: str, *, card_id: str, spec: Mapping[str, Any], market: str) -> tuple[str, dict, dict]:
    """取模板 → 拟卡 → 签1 → 开工 → 订阅存证；返回 (卡摘要, 卡, 模板)。"""
    [t] = rt.client.get_templates(provider)
    draft = rt.client.draft_card(t, card_id=card_id, spec=spec, materials={}, signers=SIGNERS, delegation_scope_digest=SCOPE_DIGEST)
    card = rt.client.sign(draft.card)
    cd = card_digest(card)
    out = rt.client.start(card)
    assert "receipt" in out, out
    rt.client.watch(cd, market)
    return cd, card, t


def wait_anchored(rt, cd: str, timeout: float = 90) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = rt.client.poll(cd)
        if any(e["type"] == "anchored" for e in events):
            return events
        time.sleep(0.5)
    raise AssertionError(f"{timeout}s 内未收到存证通知：{cd[:24]}")


def wait_until(pred, timeout: float = 90, step: float = 0.5, what: str = "条件") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(step)
    raise AssertionError(f"{timeout}s 内未满足：{what}")


def insert_need(pf, owner_id: str, assistant_id: str, text: str, *, scope: Mapping[str, Any] | None = None,
                auto_sign: int = 1, state: str = "started") -> dict:
    """直接写一条需求记录（create_need 要走模型），供 platform._on_phase 维护 orders 表。"""
    nid = "need-" + uuid.uuid4().hex[:8]
    sc = {"category": "不限", "budget": 30, "perTx": 30, "validity": "P7D", "sign1": "client", "sign7": "client",
          "nonce": uuid.uuid4().hex, "materials": [], **(scope or {})}
    pf.db.run("INSERT INTO needs (id, owner_id, assistant_id, text, scope, auto_sign, state, created, updated, trace) VALUES (?,?,?,?,?,?,?,?,?,?)",
              nid, owner_id, assistant_id, text, jdump(sc), auto_sign, state, now(), now(), "[]")
    return pf.need(nid)


def admin_token(pf) -> str:
    """管理员会话 token（不经登录接口，避免消耗登录限速配额）。"""
    return pf.auth.token("admin")


def as_admin(client, pf) -> None:
    client.cookies.set(COOKIE, admin_token(pf))
