"""九问接入：MCP 工具走完一单；连接器包合乎 jiuwenswarm 的格式。"""
import asyncio
import json
from pathlib import Path

from a2c.integrations.jiuwenswarm.mcp_server import build_server, load_identity

from .support import MARKET, PROVIDER

CONNECTOR = Path(__import__("a2c").__file__).resolve().parent / "integrations" / "jiuwenswarm" / "connector"


def test_default_provider_app_exposes_real_ap2_key_for_verified_checkout(world):
    import httpx
    from a2c.binding import A2ATransport
    from a2c.common import Directory
    from a2c.integrations.openjiuwen import provider_app
    from a2c.payment.ap2 import AP2Payment
    from .support import PAY
    card = world.formed_card('countersign')
    app = provider_app(world.provider,base_url='https://provider.example',name='provider')
    transport = A2ATransport(http=lambda:httpx.AsyncClient(transport=httpx.ASGITransport(app=app)))
    keys = Directory(fetch=transport.jwks)
    keys.add_signer(world.market.signer);keys.add_signer(world.pay_signer)
    port = AP2Payment(world.net,psp=PAY,payer=world.client_ap2,clock=world.clock,keys=keys,
                      instrument={'id':'client','type':'account'})
    voucher = port.pay(purpose='deposit',template=world.client.template(card['templateDigest']),card=card)
    assert keys.jwk(PROVIDER,world.provider_ap2.kid)==world.provider_ap2.jwk
    assert voucher['amount']==card['derived']['deposit']
    assert len(world.psp.ledger)==1


def _call(mcp, name, **args):
    out = asyncio.run(mcp.call_tool(name, args))
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


def test_mcp_tools_run_a_whole_order(world, tmp_path):
    """九问的智能体只用 MCP 工具：取模板、拟卡、签约复签、开工、轮询、取件、验收付款。"""
    mcp = build_server(world.client, tmp_path)
    t = world.publish("countersign")
    templates = _call(mcp, "list_templates", provider=PROVIDER)
    assert templates[0]["templateDigest"] == t["templateDigest"]
    novel = tmp_path / "novel.md"
    novel.write_text(world.NOVEL, encoding="utf-8")
    lic = tmp_path / "license.pdf"
    lic.write_bytes(b"%PDF-1.7 license")
    drafted = _call(mcp, "draft_card", template_digest=t["templateDigest"], card_id="C-MCP", spec=world.spec(),
                    materials={"novel": {"path": str(novel), "declared": {"format": "md", "chars": len(world.NOVEL)}},
                               "license": {"path": str(lic), "declared": {}}})
    cd = drafted["cardDigest"]
    assert drafted["inputPrecheck"] == {"novel": "pass"}
    assert _call(mcp, "sign_card", card_digest=cd)["countersigned"] is True
    assert _call(mcp, "start", card_digest=cd, market=MARKET) == {"receipt": "ok"}
    for period in (1, 2):
        world.deliver(cd, period)
        polled = _call(mcp, "poll", card_digest=cd)
        assert {"type": "anchored", "period": period} in polled["events"]
        files = _call(mcp, "fetch", card_digest=cd, period=period)["files"]
        assert all(Path(f).exists() for f in files)
        assert _call(mcp, "verify", card_digest=cd, period=period)["status"] == "pass"
        assert _call(mcp, "accept", card_digest=cd, period=period)["status"] == "pass"
    assert [e["purpose"] for e in world.psp.ledger] == ["deposit", "installment", "installment"]


def test_identity_persists(tmp_path):
    a = load_identity(tmp_path)
    b = load_identity(tmp_path)
    assert a[0].jwk == b[0].jwk and a[1].id.endswith(":user")


def test_connector_package_shape():
    """连接器包：id 与目录约定、stdio MCP、凭据表单、技能 frontmatter。"""
    manifest = json.loads((CONNECTOR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package_type"] == "mcp" and manifest["id"] == "a2c"
    assert manifest["integration"] == {"type": "stdio-mcp", "file": "mcp.json"}
    servers = json.loads((CONNECTOR / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == ["a2c"] and servers["a2c"]["command"]
    keys = {f["key"] for f in json.loads((CONNECTOR / "token-schema.json").read_text(encoding="utf-8"))["fields"]}
    assert {"A2C_HOME", "A2C_MARKETS", "A2C_PSP"} <= keys
    skill = (CONNECTOR / "skills" / "a2c-client" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: a2c-client\n")
    assert (CONNECTOR / manifest["icon"]).exists()
