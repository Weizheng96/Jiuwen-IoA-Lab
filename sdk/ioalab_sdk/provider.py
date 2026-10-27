"""Provider-side SDK glue: durable protocol state, jobs, sandbox checks and delivery."""
from __future__ import annotations

import base64
import dataclasses
import json
import secrets
import threading
import time
import uuid
from pathlib import Path

import httpx
from a2c.binding import A2ATransport
from a2c.card import card_digest, request_digest
from a2c.common import Directory, Task
from a2c.codec import utcnow, parse_duration
from a2c.jws import Signer
from a2c.provider import DefaultPolicy, Proposal, Provider
from a2c.payment.ap2 import EcKey
from a2c.verifier.acceptance import run_target
from a2c.template import acceptance_of
from .client import LabClient
from .store import LocalStore
from .templates import template_from_definition, DEFAULT_DEADLINES


class ServiceAccount:
    fetch_package = LabClient.fetch_package

    def __init__(self, base, username, password):
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=120, trust_env=False)
        self.token = ""
        self.config = self.http.get(self.base + "/api/config").raise_for_status().json()
        self.user = self.api("POST", "/token", {"username":username,"password":password})
        self.token = self.user["token"]

    api = LabClient.api

    def put(self, *, verifier_id, source, reads):
        return self.api("POST", "/provider/packages", {"verifierId":verifier_id,"source":source,"reads":reads})

    def close(self):
        self.http.close()


def service_keys(home: Path, url):
    from cryptography.hazmat.primitives.asymmetric import ed25519, ec
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, load_pem_private_key
    path = home / "keys.json"
    if not path.exists():
        home.mkdir(parents=True, exist_ok=True)
        ed = ed25519.Ed25519PrivateKey.generate()
        pay = ec.generate_private_key(ec.SECP256R1())
        value={"ed": ed.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()).hex(),
               "ec": pay.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode(), "url": url}
        path.write_text(json.dumps(value),encoding="utf-8")
        path.chmod(0o600)
    value=json.loads(path.read_text(encoding="utf-8"))
    if value["url"] != url:
        raise ValueError("Service public URL changed; use a new identity directory")
    return (Signer(url,ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(value["ed"])),"provider1"),
            EcKey("payee",load_pem_private_key(value["ec"].encode(),None)))


class DurableProvider(Provider):
    def restore_tasks(self):
        for tid, raw in self.store.all("tasks").items():
            raw["files"]={n:base64.b64decode(v) for n,v in raw.get("files",{}).items()}
            task=Task(**raw)
            if task.op in ("request.submit","card.countersign") and task.state == "working":
                task.state="failed"
                task.artifacts=[{"reason":"Service restarted before signing; retry with a new request"}]
            self.tasks[tid]=task

    def save_tasks(self):
        # The endpoint uses this same reentrant lock for task creation and updates.
        # Keep it through persistence so an older snapshot cannot overwrite a newer one.
        with self._lock:
            for tid, task in self.tasks.items():
                raw=dataclasses.asdict(task)
                raw["files"]={n:base64.b64encode(v).decode() for n,v in task.files.items()}
                self.store.put("tasks",tid,raw)

    def new_task(self, op):
        with self._lock:
            if not self._next_task_id:
                self._next_task_id="task-"+uuid.uuid4().hex
            return super().new_task(op)

    def handle(self, *args, **kwargs):
        result=super().handle(*args,**kwargs)
        self.save_tasks()
        return result

    def finish(self, *args, **kwargs):
        try:
            return super().finish(*args,**kwargs)
        finally:
            self.save_tasks()


class ProviderAgent:
    def __init__(self, definition, account: ServiceAccount, url, home, submit_job):
        self.definition, self.account, self.url, self.home = definition, account, url, Path(home)
        self.store=LocalStore(self.home/"service.sqlite")
        signer,payee=service_keys(self.home,url)
        self.transport=A2ATransport(http=lambda:httpx.AsyncClient(timeout=120,trust_env=False))
        self.provider=DurableProvider(signer,market=account.config["market"],keys=Directory(fetch=self.transport.jwks),
                                      transport=self.transport,fetch=account.fetch_package,store=self.store,
                                      ap2_key=payee,name=definition["name"])
        self.provider.restore_tasks()
        self.submit_job=submit_job
        agent=self

        class Policy(DefaultPolicy):
            def review(self, template, card, materials):
                active=sum(r.get("state") in ("formed","started","offered") for r in agent.store.all("cards").values())
                return "Service is at capacity" if active >= int(definition.get("capacity",1)) else None

            def draft(self, template, request, materials):
                if self.review(template,None,materials):
                    return "Service is at capacity"
                ref=account.put(verifier_id=definition["verifier"]["id"],source=definition["verifier"]["source"],reads=definition["verifier"].get("reads",[]))
                job=agent.enqueue("quote",request,materials)
                timeout=parse_duration(template["deadlines"]["respondWithin"]).total_seconds()
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    result=agent.store.get("jobs",job)
                    if result["state"]=="done":
                        price=result["result"]["price"]
                        if isinstance(price,bool) or not isinstance(price,(int,float)) or price<0:
                            raise ValueError("Quote must be a nonnegative fixed number")
                        name=definition["deliverable"]["name"]
                        return Proposal(acceptance={name:{"verifiers":{"script":ref},"notCovered":definition.get("notCovered",[])}},
                                        accept_within={name:definition.get("deadlines",{}).get("acceptWithin",DEFAULT_DEADLINES["acceptWithin"])},
                                        line_items={name:price})
                    if result["state"]=="failed":
                        return "Service could not draft: "+result.get("error","")[:150]
                    time.sleep(.1)
                return "Quote deadline exceeded"

            def on_start(self, template, card, materials):
                agent.enqueue("deliver",card,materials)

        self.provider.policy=Policy()
        self.template=None
        self.last_report=0

    def publish(self):
        from a2c.objects import object_digest
        digest=object_digest(self.definition)
        existing=list(self.store.all("templates").values())
        self.template=existing[-1] if existing and self.store.get('metadata','definition')==digest else self.provider.publish(template_from_definition(
            self.definition,packages=self.account,payment_provider={"id":self.account.config["pay"],
            "jwksUri":self.account.config["pay"]+"/.well-known/jwks.json"}))
        self.store.put('metadata','definition',digest)
        return self.template

    def enqueue(self, kind, obj, materials):
        digest=card_digest(obj) if kind=="deliver" else request_digest(obj)
        jid=kind+"-"+digest.split(":")[-1]
        if self.store.get("jobs",jid) is None:
            self.store.put("jobs",jid,{"id":jid,"kind":kind,"object":obj,"materials":{n:base64.b64encode(v).decode() for n,v in materials.items()},
                                       "state":"queued","token":secrets.token_urlsafe(32),"created":time.time()})
        self.submit_job(self,jid)
        return jid

    def snapshot(self, cd, op):
        event={"id":uuid.uuid4().hex,"source":"provider","actor":self.url,"op":op,"card":self.provider.card(cd)}
        self.store.put("outbox",event["id"],event)
        self.flush()

    def flush(self):
        for key,event in self.store.all("outbox").items():
            try:
                self.account.api("POST","/observations",event)
            except httpx.HTTPError:
                break
            self.store.delete("outbox",key)

    def selftest(self, cd, content):
        card=self.provider.card(cd)
        template=self.provider.template(card["templateDigest"])
        name=self.definition["deliverable"]["name"]
        files={self.definition["deliverable"].get("file",name+".json"):json.dumps(content,ensure_ascii=False).encode()}
        result=run_target(acceptance_of(template,card)[name]["verifiers"],fetch=self.account.fetch_package,template=template,card=card,
                          target=name,files=files,period=len(card["signatures"]["4"])+1,anchored_at=None)
        return {"status":result.status,"checks":[r.outcome.checks for r in result.runs],"errors":result.errors}

    def deliver(self, cd, content):
        card=self.provider.card(cd)
        if card["signatures"]["4"]:
            return {"alreadyDelivered":True}
        result=self.selftest(cd,content)
        if result["status"]!="pass" and not self.definition.get("deliver_anyway"):
            return result
        name=self.definition["deliverable"]["name"]
        self.provider.deliver(cd,{self.definition["deliverable"].get("file",name+".json"):
                                 (json.dumps(content,ensure_ascii=False).encode(),self.definition["deliverable"].get("mediaType","application/json"))})
        self.provider.save_tasks()
        self.snapshot(cd,"order.delivered")
        return {"status":"delivered"}

    def maintain(self):
        with self.provider._lock:
            for cd,rec in self.store.all("cards").items():
                card=rec["card"]
                periods=len(card['derived']['periods'])
                paid={v.get("period") for v in rec.get("vouchers",[]) if v.get("purpose")=="installment"}
                needed={i+1 for i,p in enumerate(card["derived"]["periods"]) if p["amount"]>0}
                if rec["state"]=="started" and len(card["signatures"]["7"])==periods and needed<=paid:
                    self.provider.close(cd)
                    self.provider.save_tasks()
                    self.snapshot(cd,"order.completed")
            self.provider.purge_lapsed()
        if time.monotonic()-self.last_report>15:
            jobs=list(self.store.all('jobs').values())
            detail={state:sum(j['state']==state for j in jobs) for state in ('queued','running','done','failed')}
            detail['sessions']=len(self.store.all('usage'))
            event={'id':uuid.uuid4().hex,'source':'provider','actor':self.url,'op':'service.status','detail':detail}
            self.store.put('outbox',event['id'],event)
            self.last_report=time.monotonic()
        self.flush()

    def close(self):
        self.transport.close()
        self.store.close()
