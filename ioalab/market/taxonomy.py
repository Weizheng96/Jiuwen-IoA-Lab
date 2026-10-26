"""A2X 分类树：按领域规格确定性生成，零 LLM。

A2X 检索只读 taxonomy.json（categories{id: {children, services}}）与 class.json（categories{id: {name, description, decision_rule}}）；
assignments.json 搜索不读，这里也写一份供管理员页看归属。build_config.json 的 service_hash 由 Discovery._write_hash() 在注册之后按 A2X 同法回写。
树深 1：root → 各领域叶类 → 服务（A2X 服务 id = 平台 agent id，svc-<id>）。运行期注册的服务用 assign() 确定性加入某叶类。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

VERSION = "2.0-hierarchical"
ROOT_CLASS = {"name": "全部服务", "description": "平台上全部可委托的专业服务，按领域分类"}
MISC = {"id": "cat_misc", "name": "其他", "description": "暂无明确领域归属的服务",
        "decision_rule": "需求不属于任何已列领域时在此找"}


def build(categories: Iterable[Mapping], members: Mapping[str, Iterable[str]], tdir: Path, *, reasoning: Mapping[str, str] | None = None) -> None:
    """写 taxonomy.json · class.json · assignments.json。categories 每项 {id, name, description, decision_rule}；members 叶类 id → 服务 id 列表。"""
    cats = list(categories)
    if not any(c["id"] == MISC["id"] for c in cats):
        cats.append(dict(MISC))
    tdir.mkdir(parents=True, exist_ok=True)
    taxonomy = {"version": VERSION, "root": "root", "categories": {"root": {"children": [c["id"] for c in cats], "services": []}},
                "build_status": "complete"}
    classes = {"version": VERSION, "categories": {"root": dict(ROOT_CLASS)}}
    assignments = {}
    for c in cats:
        svcs = sorted(set(members.get(c["id"], [])))
        taxonomy["categories"][c["id"]] = {"children": [], "services": svcs}
        classes["categories"][c["id"]] = {"name": c["name"], "description": c["description"], "decision_rule": c.get("decision_rule", "")}
        for sid in svcs:
            assignments[sid] = {"category_ids": [c["id"]], "reasoning": (reasoning or {}).get(sid, f"按领域文件归入 {c['name']}")}
    _write(tdir / "taxonomy.json", taxonomy)
    _write(tdir / "class.json", classes)
    _write(tdir / "assignments.json", assignments)
    cfg_path = tdir / "build_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg.update({"builder": "ioalab.market.taxonomy", "max_depth": 1})
    _write(cfg_path, cfg)


def assign(tdir: Path, service_id: str, category_id: str, *, reasoning: str = "") -> str:
    """运行期把一个服务确定性加入某叶类（不存在的叶类退到 cat_misc）；返回实际叶类 id。"""
    tax = json.loads((tdir / "taxonomy.json").read_text(encoding="utf-8"))
    cls = json.loads((tdir / "class.json").read_text(encoding="utf-8"))
    if category_id not in tax["categories"] or tax["categories"][category_id].get("children"):
        category_id = MISC["id"]
        if category_id not in tax["categories"]:
            tax["categories"]["root"]["children"].append(category_id)
            tax["categories"][category_id] = {"children": [], "services": []}
            cls["categories"][category_id] = {k: MISC[k] for k in ("name", "description", "decision_rule")}
    for cid, node in tax["categories"].items():
        if service_id in node.get("services", []) and cid != category_id:
            node["services"].remove(service_id)
    if service_id not in tax["categories"][category_id]["services"]:
        tax["categories"][category_id]["services"].append(service_id)
        tax["categories"][category_id]["services"].sort()
    _write(tdir / "taxonomy.json", tax)
    _write(tdir / "class.json", cls)
    ap = tdir / "assignments.json"
    asg = json.loads(ap.read_text(encoding="utf-8")) if ap.exists() else {}
    asg[service_id] = {"category_ids": [category_id], "reasoning": reasoning or "运行期注册时按声明的领域归入"}
    _write(ap, asg)
    return category_id


def remove(tdir: Path, service_id: str) -> None:
    tax = json.loads((tdir / "taxonomy.json").read_text(encoding="utf-8"))
    for node in tax["categories"].values():
        if service_id in node.get("services", []):
            node["services"].remove(service_id)
    _write(tdir / "taxonomy.json", tax)


def categories(tdir: Path) -> list[dict]:
    """管理员页用：各叶类及其服务数。"""
    tax = json.loads((tdir / "taxonomy.json").read_text(encoding="utf-8"))
    cls = json.loads((tdir / "class.json").read_text(encoding="utf-8"))["categories"]
    return [{"id": cid, "name": cls.get(cid, {}).get("name", cid), "services": node.get("services", [])}
            for cid, node in tax["categories"].items() if cid != "root"]


def _write(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


__all__ = ["build", "assign", "remove", "categories", "MISC"]
