"""Participant observations and verified contract projections."""
from a2c.card import card_digest
from a2c.errors import CARD_INVALID, fail
from a2c.slots import verify_card
from .store import jdump, jload, now


def party(platform, uid, card):
    client = platform.db.one("SELECT * FROM agents WHERE id=? AND role='assistant'", card.get("client", {}).get("id", ""))
    provider = platform._agent_of_provider(card.get("provider", ""))
    if not client or not provider or uid not in (client["owner_id"], provider["owner_id"]):
        fail(CARD_INVALID, "The authenticated account is not a registered party to this contract")
    meta = jload(client["meta"])
    if card["client"]["jwk"] != meta.get("clientJwk") or (card.get("user") and card["user"]["jwk"] != meta.get("userJwk")):
        fail(CARD_INVALID, "Contract keys do not match enrollment")
    template = platform.market.market._template(card["templateDigest"])
    chain = verify_card(template, card, platform.keys)
    return client, provider, template, chain


def observe(platform, uid, body):
    eid, op = str(body.get("id", "")), str(body.get("op", ""))
    source = body.get("source")
    if not eid or len(eid) > 100 or len(op) > 100 or source not in ("client", "provider", "experiment"):
        fail(CARD_INVALID, "Invalid observation")
    card = body.get("card")
    client = None
    if card:
        client, provider, template, chain = party(platform, uid, card)
        if source == "provider" and provider["owner_id"] != uid:
            fail(CARD_INVALID, "Only the service owner may report provider events")
        if source != "provider" and client["owner_id"] != uid:
            fail(CARD_INVALID, "Only the client owner may report client events")
        actor = card["provider"] if source == "provider" else client["id"]
    else:
        actor = str(body.get("actor", ""))
        row = platform.db.one("SELECT owner_id FROM agents WHERE id=?", actor) or platform._agent_of_provider(actor)
        if not row or row["owner_id"] != uid:
            fail(CARD_INVALID, "Observation actor does not belong to the authenticated account")
        if source=='provider' and not platform._agent_of_provider(actor):
            fail(CARD_INVALID,'Provider observations require a registered service')
    cd, stale = card_digest(card) if card else None, False
    evidence={}
    with platform.db.tx() as conn:
        previous = conn.execute("SELECT owner_id FROM observations WHERE id=?", (eid,)).fetchone()
        if previous:
            if previous["owner_id"] != uid:
                fail(CARD_INVALID, "Observation id belongs to another account")
            return {"ok": True, "duplicate": True}
        if card:
            old = conn.execute("SELECT detail,state FROM orders WHERE card_digest=?", (cd,)).fetchone()
            oldcard = jload(old["detail"]).get("card") if old else None
            def count(value):
                return {k: sum(bool(x) for x in v) if isinstance(v, list) else int(bool(v)) for k, v in value["signatures"].items()}
            counts = count(card)
            stale = bool(oldcard and any(counts[k] < n for k, n in count(oldcard).items()))
            if not stale:
                state = "started" if card["fulfillment"].get("receipt") else "forming"
                ended = platform.market.market.store.get("ledger", cd)
                if ended:
                    state = ended.get("outcome", state)
                elif old and old["state"] in ("completed", "arbitrated", "client-terminated"):
                    state = old["state"]
                conn.execute("INSERT INTO orders (card_digest,need_id,owner_id,assistant_id,provider_id,template_digest,formation,total,state,phase,created,updated,detail) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(card_digest) DO UPDATE SET state=excluded.state,phase=excluded.phase,updated=excluded.updated,detail=excluded.detail",
                             (cd, None, client["owner_id"], client["id"], card["provider"], card["templateDigest"], template["formation"],
                              str(card["derived"]["total"]), state, op, now(), now(), jdump({"card": card, "source": source, "trust": "verified"})))
                evidence={'assistant':client['id'],'provider':card['provider'],'state':state,
                          'signatures':{k:[bool(x) for x in v] if isinstance(v,list) else bool(v) for k,v in card['signatures'].items()},
                          'total':str(card['derived']['total']),'periods':len(card['derived']['periods']),'formation':template['formation']}
        if source=='provider':
            service=platform._agent_of_provider(actor)
            meta=jload(service['meta']);meta['lastSeen']=now()
            if op=='service.status':
                data=body.get('detail',{})
                meta['execution']={k:data.get(k) for k in ('queued','running','done','failed','sessions')
                                   if isinstance(data.get(k),int) and data[k]>=0}
            conn.execute('UPDATE agents SET meta=? WHERE id=?',(jdump(meta),service['id']))
        conn.execute("INSERT INTO observations (id,owner_id,at) VALUES (?,?,?)", (eid, uid, now()))
    if not stale:
        platform.bus.emit("observation", op, sender=actor, receiver=card.get("provider", "") if card and source != "provider" else "",
                          card_digest=cd,period=body.get('detail',{}).get('period'),outcome=str(body.get('detail',{}).get('status','')),
                          detail={"source": source, "trust": "reported", "evidenceTrust":"verified" if card else None,
                                  **evidence,"data": body.get("detail", {})})
    return {"ok": True, "stale": stale}
