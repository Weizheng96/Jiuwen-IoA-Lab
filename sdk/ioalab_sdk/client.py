"""User-owned contract execution; REST carries identity and observations only."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path

import httpx
from a2c.binding import A2ATransport
from a2c.card import card_digest
from a2c.client import Client
from a2c.codec import utcnow, digest_bytes
from a2c.common import Directory, Store
from a2c.integrations.jiuwenswarm.mcp_server import load_identity
from a2c.jws import sign
from a2c.objects import object_digest
from a2c.payment.ap2 import AP2Payment
from .store import LocalStore
from .transport import TaskPollingTransport
from .lease import Lease


from .scope import DelegationScopes


class LabClient(DelegationScopes):
    def __init__(self, base: str, username: str, password: str, home: Path, *, source: str = "client"):
        self.base, self.username, self.password = base.rstrip("/"), username, password
        if source not in ("client", "experiment"):
            raise ValueError("source must be client or experiment")
        self.source, self.home = source, Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.lease=Lease(self.home/'client.lock',timeout=120)
        try:
            self._connect(username,password)
        except BaseException:
            self.close()
            raise

    def _connect(self, username, password):
        self.http = httpx.Client(timeout=120, trust_env=False)
        self.token = ""
        self.transport = TaskPollingTransport(http=lambda: httpx.AsyncClient(timeout=120, trust_env=False))
        self.signer, self.user, self.payer = load_identity(self.home)
        self.store = LocalStore(self.home / "contracts.sqlite")
        self.outbox = LocalStore(self.home / "outbox.sqlite")
        self.client = None
        self.config = self.http.get(self.base + "/api/config").raise_for_status().json()
        cache_key=self.base+'|'+username
        self.account=self.store.get('auth',cache_key)
        if self.account: self.token=self.account['token']
        try: self.api('GET','/me')
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code!=401: raise
            self.account=self.api('POST','/token',{'username':username,'password':password})
            self.token=self.account['token']
            self.store.put('auth',cache_key,self.account)
        challenge = self.api("GET", "/identity/challenge")
        payload = {"challenge": challenge["challenge"], "owner": self.account["id"], "platform": self.base,
                   "clientId": self.signer.id, "clientJwk": self.signer.jwk, "userJwk": self.user.jwk,
                   "payerJwk": self.payer.jwk, "source": self.source}
        self.api("POST", "/identity", {**payload, "proof": sign(object_digest(payload), self.signer, "client", utcnow())})
        self.client = Client(self.signer, keys=Directory(fetch=self.transport.jwks), transport=self.transport,
                             fetch=self.fetch_package, store=self.store, user=self.user,
                             payment_provider={"id": self.config["pay"], "jwksUri": self.config["pay"] + "/.well-known/jwks.json"},
                             payment=AP2Payment(self.transport, psp=self.config["pay"], payer=self.payer, clock=utcnow,
                                                instrument={"id": "ioalab-" + self.account["id"], "type": "wallet"}),
                             markets=(self.config["market"],))
        import os
        if os.environ.get('IOALAB_VERIFIER_MODEL'):
            from .observation import model_port
            self.client.model=model_port(os.environ.get('IOALAB_LLM_BASE_URL'),os.environ.get('IOALAB_LLM_API_KEY'),os.environ['IOALAB_VERIFIER_MODEL'])
        self.flush()

    def delegate(self, action, task_id=None, confirmed=False, **kwargs):
        from .workflow import Workflow
        previous=getattr(self,'_workflow_dispatch',False)
        self._workflow_dispatch=True
        try:return Workflow(self).run(action,task_id=task_id,confirmed=confirmed,**kwargs)
        finally:self._workflow_dispatch=previous

    def require_workflow_dispatch(self, cd):
        task_id=self.store.get('workflow_cards',cd)
        if task_id and not getattr(self,'_workflow_dispatch',False):
            raise ValueError('Use delegate with task_id='+task_id+' for this managed contract; do not bypass its human gates')

    def inspect(self, cd):
        card=self.client.card(cd)
        return {'card':card,'template':self.client.template(card['templateDigest']),
                'scopeId':self.store.get('card_scopes',cd)}

    def reputation(self, provider):
        result=self.api('GET','/providers/reputation?provider='+__import__('urllib.parse',fromlist=['quote']).quote(provider,safe=''))
        from a2c.jws import verify
        from a2c.objects import object_digest
        from a2c.jws import header_of
        sig=result['signature'];header=header_of(sig)
        key=self.client.keys.jwk(self.config['market'],header.get('kid'))
        verify(sig,object_digest({k:v for k,v in result.items() if k!='signature'}),key,slot='market',signer=self.config['market'],expect_kid=True)
        return result

    def api(self, method: str, path: str, body=None):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        r = self.http.request(method, self.base + "/api/v1" + path, json=body, headers=headers)
        r.raise_for_status()
        return r.json()

    def fetch_package(self, uri: str) -> bytes:
        prefix = "ioalab://pkg/sha256:"
        if not uri.startswith(prefix):
            raise ValueError("Only IoA content-addressed verifier packages are supported")
        digest = uri[len(prefix):]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid package digest")
        blob = self.http.get(self.base + "/api/v1/packages/sha256:" + digest).raise_for_status().content
        if digest_bytes(blob) != "sha256:" + digest:
            raise ValueError("Verifier package digest mismatch")
        return blob

    def observe(self, op: str, cd: str | None = None, **detail):
        event = {"id": uuid.uuid4().hex, "source": self.source, "actor": self.signer.id, "op": op, "detail": detail}
        if cd:
            event["card"] = self.client.card(cd)
        self.outbox.put("pending", event["id"], event)
        self.flush()

    def flush(self):
        for eid, event in self.outbox.all("pending").items():
            try:
                self.api("POST", "/observations", event)
            except (httpx.HTTPError, ValueError):
                break
            self.outbox.delete("pending", eid)

    def search(self, query: str, top_k: int = 8):
        return self.api("POST", "/search", {"query": query, "top_k": top_k})

    def templates(self, provider: str):
        return self.client.get_templates(provider)

    def compare(self, providers: list[str], reason: str, selected: str, cd: str | None = None):
        """Read complete signed templates; record the assistant's choice as reported."""
        if not isinstance(providers,list) or not 2<=len(providers)<=8 or len(set(providers))!=len(providers):
            raise ValueError('Compare 2–8 distinct provider endpoints')
        if selected not in providers or not isinstance(reason,str) or not 1<=len(reason)<=1200:
            raise ValueError('A candidate and a bounded comparison reason are required')
        templates=[{'provider':p,'templates':self.templates(p)} for p in providers]
        if cd and self.client.card(cd)['provider']!=selected:
            raise ValueError('Selected provider does not match this contract')
        result={'candidates':templates,'selected':selected,'reason':reason,'trust':'reported'}
        self.store.put('comparisons',cd or uuid.uuid4().hex,result)
        self.observe('discovery.compared',cd,providers=providers,selected=selected,reason=reason)
        return result

    @staticmethod
    def clean_spec(template, spec):
        result, notices = {}, []
        deliverables=template["deliverables"]
        if not isinstance(spec,dict): raise ValueError('spec must be an object keyed by deliverable name')
        if len(deliverables)==1 and deliverables[0]['name'] not in spec:
            item=deliverables[0];schema=item.get('specSchema',{});fields=schema.get('properties',schema)
            if spec and all(k in fields for k in spec):
                spec={item['name']:spec};notices.append('Mapped flat spec to the unique deliverable')
        if len(deliverables)==1 and len(spec)==1 and deliverables[0]["name"] not in spec:
            if not isinstance(next(iter(spec.values())),dict):
                raise ValueError('spec must have shape '+json.dumps({deliverables[0]['name']:{'<field>':'<value>'}}))
            spec={deliverables[0]["name"]:next(iter(spec.values()))}
            notices.append("Mapped values to the unique deliverable")
        for item in deliverables:
            name=item["name"]; schema=item.get("specSchema",{}); values={}
            if not isinstance(spec.get(name,{}),dict): raise ValueError('spec.'+name+' must be an object containing deliverable fields')
            for key,value in spec.get(name,{}).items():
                if key not in schema:
                    notices.append("Removed unknown field "+key);continue
                rule=schema[key]
                if "const" in rule:
                    notices.append("Removed const field "+key);continue
                if isinstance(value,str) and rule.get("type") in ("integer","number"):
                    try: value=int(value) if rule["type"]=="integer" else float(value)
                    except ValueError: pass
                values[key]=value
            result[name]=values
        return result,notices

    def resolve_template(self, reference):
        if reference.startswith(("http://","https://")):
            templates=self.templates(reference)
            if len(templates)!=1: raise ValueError("Choose an exact template digest")
            return templates[0]
        return self.client.template(reference)

    def check_budget(self, cd):
        from decimal import Decimal
        limit=self.store.get("budgets",cd)
        if limit is not None and Decimal(str(self.client.card(cd)["derived"]["total"]))>Decimal(limit):
            raise ValueError("Contract exceeds the authorized budget")

    def draft(self, template_digest: str, spec: dict, *, materials=None, budget=100, sign1="user", sign7="user"):
        template = self.resolve_template(template_digest)
        spec, notices = self.clean_spec(template,spec)
        draft = self.client.draft_card(template, card_id=uuid.uuid4().hex, spec=spec, materials=materials or {},
                                      signers={"slot1": sign1, "slot7": sign7},
                                      delegation_scope_digest=object_digest({"budget": str(budget)}))
        from decimal import Decimal
        if Decimal(str(draft.card["derived"]["total"])) > Decimal(str(budget)):
            self.store.delete("cards", card_digest(draft.card))
            raise ValueError("Contract exceeds the authorized budget")
        cd = card_digest(draft.card)
        self.store.put("budgets",cd,str(budget))
        self.observe("order.drafted", cd)
        return {"cardDigest": cd, "derived": draft.card["derived"], "notices": draft.notices+notices}

    @staticmethod
    def require_confirmation(confirmed):
        if confirmed is not True:
            raise ValueError("User confirmation is required")

    def sign(self, cd: str, *, confirmed=False):
        self.require_workflow_dispatch(cd)
        self.require_confirmation(confirmed)
        self.check_budget(cd)
        card = self.client.card(cd)
        if card["signatures"].get("1") is None:
            card = self.client.sign(card)
        template = self.client.template(card["templateDigest"])
        if template["formation"] == "countersign" and card["signatures"].get("2") is None:
            result = self.client.countersign(card)
            if isinstance(result, str):
                return {"declined": result}
        self.observe("order.signed", cd)
        return {"cardDigest": cd}

    def request(self, template_digest: str, spec: dict, *, materials=None, budget=100, scope_id=None, confirmed=False):
        self.require_confirmation(confirmed)
        managed=self.store.get('workflow_scopes',scope_id) if scope_id else None
        if managed and not getattr(self,'_workflow_dispatch',False):
            raise ValueError('Use delegate with task_id='+managed+' to resume this authorized proposal collection')
        t = self.resolve_template(template_digest)
        spec, _ = self.clean_spec(t,spec)
        scope_terms=self.live_scope(scope_id) if scope_id else {'budget':str(budget)}
        if scope_id: scope_terms={k:scope_terms[k] for k in ('scopeId','purpose','budget','proposalFeeLimit','expiresAt')}
        req = self.client.draft_request(t, request_id=uuid.uuid4().hex, spec=spec, materials=materials or {},
                                        signers={"slot1": "user", "slot7": "user"},
                                        delegation_scope_digest=object_digest(scope_terms))
        scope=key=None
        if scope_id:
            scope=self.live_scope(scope_id)
            if __import__('decimal').Decimal(str(budget))!=__import__('decimal').Decimal(scope['budget']):
                raise ValueError('Use the authorized total budget')
            scope,key,cached=self.reserve_proposal(scope_id,t,object_digest({'spec':req['spec'],'inputs':req['inputs']}))
            if cached is not None: return cached
        card = self.client.submit_request(req)
        if isinstance(card, str):
            result={'returned':card}
            if scope_id: self.settle_proposal(scope,key,result)
            return result
        self.store.put("budgets",card_digest(card),str(budget))
        if scope_id: self.settle_proposal(scope,key,{'cardDigest':card_digest(card),'derived':card['derived']})
        from decimal import Decimal
        if Decimal(str(card["derived"]["total"])) > Decimal(str(budget)):
            return {"overBudget": True, "cardDigest": card_digest(card), "derived": card["derived"]}
        cd = card_digest(card)
        result={"cardDigest":cd,"derived":card['derived']}
        self.observe("order.proposed", cd)
        return result

    def adopt(self, cd: str, *, confirmed=False):
        self.require_workflow_dispatch(cd)
        self.require_confirmation(confirmed)
        self.check_budget(cd)
        self.check_scope_selection(cd)
        if not self.client.card(cd)["signatures"].get("1"):
            self.client.adopt(self.client.card(cd))
        self.observe("order.signed", cd)
        return {"cardDigest": cd}

    def start(self, cd: str, *, confirmed=False):
        self.require_workflow_dispatch(cd)
        self.require_confirmation(confirmed)
        self.check_budget(cd)
        rec = self.store.get("cards", cd)
        if rec.get("state") != "started":
            result = self.client.start(self.client.card(cd))
            if "receipt" not in result:
                return result
        self.client.watch(cd, self.config["market"])
        self.observe("order.started", cd)
        return {"cardDigest": cd, "state": "started"}

    def poll(self, cd: str):
        from a2a.utils.errors import TaskNotFoundError
        try:
            events = self.client.poll(cd)
        except TaskNotFoundError:
            # Market watch tasks are ephemeral; a server restart requires a fresh subscription.
            self.client.watch(cd,self.config['market'])
            events=self.client.poll(cd)
        rec=self.store.get("cards",cd)
        # A previously consumed notification must still lead to a local fetch after a crash.
        signed=len(rec["card"]["signatures"]["6"])
        for notice in rec.get("notices",[]):
            if notice["period"]>signed and not any(e.get("type")=="anchored" and e.get("period")==notice["period"] for e in events):
                events.append({"type":"anchored","period":notice["period"]})
        # Current card state remains the source when a poll response or observer upload was lost.
        status = self.client.status(cd)
        self.flush()
        return {"events": events, "phase": status.phase, "card": self.client.card(cd)}

    def watch(self, cd: str, *, timeout=60, interval=1):
        """Wait locally without model turns; never fetch, accept or pay implicitly."""
        import time
        until=time.monotonic()+min(max(float(timeout),0),300)
        while True:
            result=self.poll(cd)
            if result['events'] or time.monotonic()>=until: return result
            time.sleep(min(max(float(interval),.1),5))

    def fetch(self, cd: str, period: int):
        self.require_workflow_dispatch(cd)
        record=self.store.get('cards',cd)
        cache_hit=len(record['card']['signatures'].get('6',[]))>=period
        if cache_hit:
            cached=record.get('deliveries',{}).get(str(period))
            if not cached:raise ValueError('Delivery receipt exists but local files require reconciliation')
            files={n:base64.b64decode(b) for n,b in cached.items()}
            manifest=record['card']['fulfillment']['periods'][period-1]['manifest']['files']
            if {n:digest_bytes(b) for n,b in files.items()}!={f['name']:f['digest'] for f in manifest}:
                raise ValueError('Cached delivery does not match the signed manifest')
        else:files = self.client.fetch_delivery(cd, period)
        folder = self.home / "deliveries" / cd.split(":")[-1] / str(period)
        folder.mkdir(parents=True, exist_ok=True)
        for name, blob in files.items():
            if Path(name).name != name or name in (".", ".."):
                raise ValueError("Unsafe delivery filename")
            (folder / name).write_bytes(blob)
        if not cache_hit:self.observe("order.fetched", cd, period=period)
        return {n: str(folder / n) for n in files}

    def verify(self, cd: str, period: int):
        self.require_workflow_dispatch(cd)
        result = self.client.verify(cd, period)
        self.observe("order.verified", cd, period=period, status=result.status)
        return {"status": result.status, "notBefore": str(result.not_before) if result.not_before else None,
                "errors": result.errors, "checks": [r.outcome.checks for r in result.runs]}

    def accept(self, cd: str, period: int, *, confirmed=False):
        self.require_workflow_dispatch(cd)
        self.require_confirmation(confirmed)
        if len(self.client.card(cd)["signatures"]["7"]) >= period:
            vouchers = self.store.get("cards", cd).get("vouchers", [])
            paid = any(v.get("purpose") == "installment" and v.get("period") == period for v in vouchers)
            return {"status": "already-accepted", "payment": "recorded" if paid else "requires-reconciliation"}
        try:
            result = self.client.accept(cd, period)
        except Exception:
            self.observe("order.payment-pending",cd,period=period)
            raise
        self.observe("order.accepted" if result.status == "pass" else "order.verified", cd, period=period, status=result.status)
        return {"status": result.status, "voucher": result.voucher}

    def pay(self, cd: str, period: int, *, confirmed=False):
        self.require_workflow_dispatch(cd)
        self.require_confirmation(confirmed)
        card=self.client.card(cd)
        if len(card["signatures"]["7"])<period:
            raise ValueError("Cannot pay before verified acceptance")
        view=self.api("GET","/orders/"+cd)
        if period in view.get("paidPeriods",[]):
            return {"status":"requires-reconciliation"}
        template=self.client.template(card["templateDigest"])
        voucher=self.client.payment.pay(purpose="installment",template=template,card=card,period=period)
        self.client.transport.send(card["provider"],"payment.notify",{"voucher":voucher})
        rec=self.store.get("cards",cd);rec.setdefault("vouchers",[]).append(voucher);self.store.put("cards",cd,rec)
        self.observe("order.paid",cd,period=period)
        return {"status":"paid","voucher":voucher}

    def answer_step(self, cd: str, granted: bool, *, confirmed=False):
        self.require_workflow_dispatch(cd)
        self.require_confirmation(confirmed)
        record=self.store.get('cards',cd)
        steps=record.get('steps',[])
        if steps and all(step.get('answer') is not None for step in steps):
            answer=steps[-1]['answer']
            if answer.get('granted') is not granted:raise ValueError('This step already has a different authorization')
            return answer
        result = self.client.answer_step(cd, granted)
        self.observe("order.authorized", cd)
        return result

    def terminate(self, cd: str, *, confirmed=False):
        self.require_confirmation(confirmed)
        result = self.client.terminate(cd, quote=self.client.termination_quote(cd))
        self.observe("order.terminated", cd)
        return result

    def arbitrate(self, cd: str, period: int, *, confirmed=False):
        self.require_confirmation(confirmed)
        rec = self.store.get("cards", cd)
        files = {k: base64.b64decode(v) for k, v in rec.get("deliveries", {}).get(str(period), {}).items()}
        return self.api("POST", "/arbitrations", {"card": rec["card"], "period": period,
                        "files": {k: base64.b64encode(v).decode() for k, v in files.items()}, "ground": "acceptance-failed"})

    def close(self):
        for name in ('transport','http','store','outbox','lease'):
            resource=getattr(self,name,None)
            if resource: resource.close()
