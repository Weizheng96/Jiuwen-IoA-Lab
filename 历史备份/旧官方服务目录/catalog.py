"""领域文件：official-services/domains/<nn>-<slug>.json，每个 = 一个 ISP 账号负责的领域 + 它名下的服务定义。

```json
{"slug": "health", "label": "医疗健康", "account": "ISP_001",
 "category": {"id": "cat_health", "name": "医疗健康", "description": "…", "decision_rule": "…"},
 "services": [ 服务定义… ]}
```

服务定义沿用 seed/README.md 的 schema；验收器两种写法：
- 声明式：`verifier: {id, file?, checks: [...]}`，由 verifiers/common.py 的通用引擎执行，`reads` 自动从 checks 里引用的 spec./inputs. 推出；
- 脚本：`verifier: {id, reads, script: "<name>.py"}`，源码取 verifiers/<name>.py。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
DOMAINS_DIR = HERE / "domains"
VERIFIERS_DIR = HERE / "verifiers"
_REF = re.compile(r"\b(?:spec|inputs)\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b")


def domain_files() -> list[Path]:
    return sorted(DOMAINS_DIR.glob("*.json")) if DOMAINS_DIR.exists() else []


def load_domains() -> list[dict]:
    out = []
    for p in domain_files():
        d = json.loads(p.read_text(encoding="utf-8"))
        for k in ("slug", "label", "account", "category", "services"):
            assert k in d, f"{p.name} 缺 {k}"
        cat = d["category"]
        assert cat.get("id", "").startswith("cat_") and cat.get("name") and cat.get("description"), f"{p.name} category 不完整"
        d["services"] = [{**materialize(s, p.stem), "category": cat["id"], "domain": d["slug"]} for s in d["services"]]
        d["file"] = p.name
        out.append(d)
    slugs = [d["slug"] for d in out]
    assert len(slugs) == len(set(slugs)), "领域 slug 重复"
    ids = [s["id"] for d in out for s in d["services"]]
    assert len(ids) == len(set(ids)), "服务 id 跨领域重复"
    return out


def materialize(s: dict, origin: str) -> dict:
    """把验收器写法统一成运行时要的 verifier {id, reads, source}；补 runnable / capacity 默认。"""
    s = dict(s)
    v = dict(s.get("verifier") or {})
    if "checks" in v:
        rules = {"file": v.get("file") or s["deliverable"].get("file") or (s["deliverable"]["name"] + ".json"), "checks": v["checks"]}
        reads = sorted(set(v.get("reads", [])) | set(_REF.findall(json.dumps(rules, ensure_ascii=False))))
        v = {"id": v["id"], "reads": reads, "source": common_source().replace("__RULES__", json.dumps(rules, ensure_ascii=False))}
    elif "script" in v:
        src = (VERIFIERS_DIR / v["script"]).read_text(encoding="utf-8")
        if "__CRITERIA__" in src:
            criteria=v.get("criteria")
            if not isinstance(criteria,list) or not criteria or not all(isinstance(x,str) and x for x in criteria):
                raise ValueError("Verifier criteria must be nonempty text items")
            src=src.replace("__CRITERIA__",json.dumps(criteria,ensure_ascii=False))
        v = {**v, "reads": list(v.get("reads", [])), "source": src}
    elif v and "source" not in v:
        raise ValueError(f"{origin}/{s.get('id')}：verifier 须有 checks 或 script")
    if v:
        s["verifier"] = v
    top = [c for c in (s.get("verifier", {}).get("checks") if "checks" in (s.get("verifier") or {}) else []) or [] if c.get("kind") == "keys" and not c.get("path")]
    if top and s.get("generator", {}).get("system") and "顶层键必须恰好" not in s["generator"]["system"]:
        s["generator"]["system"] += f"\n输出 JSON 的顶层键必须恰好是：{'、'.join(top[0]['required'])}（不得改名、不得缩写）。"
    s.setdefault("runnable", True)
    s.setdefault("capacity", 1 if s.get("formation") in ("countersign", "proposal") else 3)
    if s.get("formation")=="proposal":
        note="报价约定：proposalFee仅为拟定合同的费用，不是交付总价；提交request取得服务方签署的提案后，才以合同derived.total作为正式报价。"
        if note not in s["description"]:s["description"]+=" "+note
        dl=dict(s["deliverable"])
        desc=dl.get("description",dl.get("title",dl["name"]))
        if note not in desc:dl["description"]=desc+" "+note
        s["deliverable"]=dl
    criteria=s.get("verifier",{}).get("criteria")
    if criteria:
        dl=dict(s["deliverable"])
        dl["description"]+=" 可复核验收判据："+"；".join(str(i+1)+"."+text for i,text in enumerate(criteria))+"。"
        dl["description"]+="执行方式："+("模型观察源码中的三类功能，由WASM程序检查布尔观察结果；不运行交付行为测试。" if s["verifier"].get("kind")=="skill" else "在WASM中直接调用交付源码；竞争请求及重试按顺序模拟，不是实际网络故障或多线程压力试验。")
        s["deliverable"]=dl
    s["origin"] = origin
    return s


_COMMON: str | None = None


def common_source() -> str:
    global _COMMON
    if _COMMON is None:
        _COMMON = (VERIFIERS_DIR / "common.py").read_text(encoding="utf-8")
    return _COMMON


def definitions() -> list[dict]:
    """全部服务定义（跨领域，按文件顺序）。"""
    return [s for d in load_domains() for s in d["services"]]


def account_of() -> dict[str, str]:
    """服务 id → 负责它的 ISP 用户名。"""
    return {s["id"]: d["account"] for d in load_domains() for s in d["services"]}


def category_of() -> dict[str, str]:
    """服务 id → 分类树叶类 id。"""
    return {s["id"]: d["category"]["id"] for d in load_domains() for s in d["services"]}


__all__ = ["load_domains", "definitions", "account_of", "category_of", "domain_files", "materialize", "common_source"]
