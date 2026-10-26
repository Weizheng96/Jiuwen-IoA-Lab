# -*- coding: utf-8 -*-
"""核对种子服务：每份定义（8 个领域 × 6）能生成合法模板、验收器包能打包、sample 在沙箱里过验收器。

    python tools/check_services.py            # 全部
    python tools/check_services.py wechat-posts
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from a2c.card import build_card, build_proposal_card, build_request, validate_card  # noqa: E402
from a2c.template import validate  # noqa: E402
from a2c.verifier.acceptance import run_target  # noqa: E402
from ioalab.runtime.packages import PackageStore  # noqa: E402
from ioalab.runtime.service import template_from_definition  # noqa: E402
from ioalab.seed import load_definitions  # noqa: E402

PAY = {"id": "http://127.0.0.1:8750/a2a/pay", "jwksUri": "http://127.0.0.1:8750/a2a/pay/.well-known/jwks.json"}


def check(d: dict, packages: PackageStore) -> list[str]:
    errs: list[str] = []
    t = template_from_definition(d, packages=packages, payment_provider=PAY)
    t["provider"] = "http://127.0.0.1:8750/a2a/providers/svc-" + d["id"]
    from a2c.template import template_digest
    t["templateDigest"] = template_digest(t)
    from a2c.jws import Signer
    from a2c.slots import sign_template
    from a2c.codec import utcnow
    t["signatures"] = {}
    t = sign_template(t, Signer.generate(t["provider"], kid="k1"), "provider", utcnow())
    try:
        validate(t, endorsed=False)
    except Exception as e:  # noqa: BLE001
        return [f"模板不合法：{e}"]
    if not d.get("runnable"):
        return errs
    if "sample" not in d:
        return ["可运行的服务缺 sample"]
    name = d["deliverable"]["name"]
    spec = {name: d.get("sample_spec") or {}}
    inputs = {}
    for i in d.get("inputs", []):
        inputs[i["name"]] = (b"x" * 10, (d.get("sample_inputs") or {}).get(i["name"], {}))
    terms = dict(client={"id": "urn:x", "jwk": {"kty": "OKP", "crv": "Ed25519", "x": "A" * 43}},
                 signers={"slot1": "client", "slot7": "client"}, spec=spec,
                 inputs={n: {**decl, "digest": "sha256:" + "0" * 64} for n, (_b, decl) in inputs.items()},
                 delegation_scope_digest="sha256:" + "0" * 64, payment_provider=PAY)
    try:
        if d.get("formation") == "proposal":          # 提案型：服务方按提案卡拟卡，报价取区间下限（与运行时桩一致）
            ref = packages.put(verifier_id=d["verifier"]["id"], source=d["verifier"]["source"], reads=d["verifier"].get("reads", []))
            refs = {"script": ref}
            req = build_request(t, request_id="R-CHECK", version=1, **terms)
            card = build_proposal_card(t, req, card_id="C-CHECK", acceptance={name: {"verifiers": refs, "notCovered": []}},
                                       accept_within={name: (d.get("deadlines") or {}).get("acceptWithin", "PT15M")},
                                       line_items={name: int((d.get("pricing") or {}).get("range", [10, 30])[0])})
            validate_card(t, card, request=req)
        else:
            refs = t["acceptance"][name]["verifiers"]
            card = build_card(t, card_id="C-CHECK", **terms)
            validate_card(t, card)
    except Exception as e:  # noqa: BLE001
        return [f"按 sample_spec / sample_inputs 拟卡失败：{e}"]
    fname = d["deliverable"].get("file", name + ".json")
    files = {fname: json.dumps(d["sample"], ensure_ascii=False).encode("utf-8")}
    result = run_target(refs, fetch=packages.fetch, template=t, card=card, target=name, files=files, period=1, model=None)
    if result.status != ("fail" if d.get("expect_fail") else "pass"):
        errs.append(f"sample 验收结果 {result.status}：{[(r.ref['id'], r.outcome.status, r.outcome.checks, r.outcome.error) for r in result.runs]}")
    return errs


def main() -> int:
    only = sys.argv[1:] or None
    packages = PackageStore(Path(tempfile.mkdtemp()) / "pkg")
    bad = 0
    defs = [d for d in load_definitions() if not only or d["id"] in only]
    for d in defs:
        errs = check(d, packages)
        print(("  ✓ " if not errs else "  ✗ ") + f"{d['id']:<24} {d['name']}" + ("" if not errs else "：" + "；".join(errs)))
        bad += bool(errs)
    print(f"{len(defs) - bad}/{len(defs)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
