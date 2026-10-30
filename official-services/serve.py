"""Independent official Provider process. Uses only the public SDK and protocol endpoints."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src/access/sdk"))
from a2c.binding import build_app, A2C_EXTENSION, AP2_EXTENSION
from a2c.card import card_digest
from a2c.codec import parse_duration
from fastapi import FastAPI, HTTPException, Request
from ioalab_sdk.provider import ProviderAgent, ServiceAccount
from ioalab_sdk.provider_app import build_provider_app
from catalog import load_domains
from swarm import SwarmEngine


class ServiceHost:
    def __init__(self, *, platform, public_url, data, stub=False, only=None, concurrency=2, engine=None, scenario=None):
        self.platform,self.public_url,self.data,self.stub=platform.rstrip("/"),public_url.rstrip("/"),Path(data),stub
        self.concurrency=max(1,int(concurrency))
        self.engine=engine or SwarmEngine()
        self.queue=queue.Queue()
        self.stopping=threading.Event()
        self.agents={}
        self.accounts={}
        self.workers=[]
        self.pending=set()
        self.lock=threading.Lock()
        self.ready=False
        self.error=""
        self.definitions=[(domain["account"],d) for domain in load_domains() for d in domain["services"] if not only or d["id"] in only]
        if scenario:
            if scenario!='nail-booking': raise ValueError('Unknown service scenario')
            from scenario import nail_services
            self.definitions=[(account,d) for account,d in nail_services() if not only or d['id'] in only]
        self.health={"running":0,"done":0,"failed":0}
        self.port=int(__import__('urllib.parse',fromlist=['urlparse']).urlparse(public_url).port or 8751)
        self.local_url=f"http://127.0.0.1:{self.port}"

    def submit(self, agent, jid):
        key=(agent.url,jid)
        with self.lock:
            if key in self.pending or agent.store.get("jobs",jid)["state"] in ("done","failed"):
                return
            self.pending.add(key)
            self.queue.put((agent,jid))

    def bootstrap(self, app):
        from ioalab_sdk.lease import Lease
        self.lease=Lease(self.data/'host.lock',timeout=0)
        if not self.stub:
            self.engine.check()
        for username,d in self.definitions:
            if username not in self.accounts:
                self.accounts[username]=ServiceAccount(self.platform,username,username.split("_")[-1],self.data/'accounts'/username)
            account=self.accounts[username]
            sid="svc-"+d["id"]
            url=self.public_url+"/a2a/providers/"+sid
            agent=ProviderAgent(d,account,url,self.data/sid,self.submit)
            self.agents[sid]=agent
            app.mount("/a2a/providers/"+sid,build_provider_app(agent.provider,name=d["name"],base_url=url))
            row=account.api("POST","/provider/register",{"url":url,"name":d["name"],"description":d["description"],
                            "tags":d.get("tags",[]),"category":d["category"],"ap2Jwk":agent.provider.ap2_key.jwk})
            agent.registration_id=row["id"]
            agent.refresh_template()
            for jid,job in agent.store.all("jobs").items():
                if job["state"]=="running":
                    job["state"]="queued"
                    agent.store.put("jobs",jid,job)
                if job["state"]=="queued":
                    self.submit(agent,jid)
        for i in range(self.concurrency):
            thread=threading.Thread(target=self.work,name=f"ioa-service-{i}",daemon=True)
            self.workers.append(thread)
            thread.start()
        self.ready=True

    def work(self):
        while not self.stopping.is_set():
            try:
                agent,jid=self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            job=agent.store.get("jobs",jid)
            try:
                if job["state"] in ("done","failed"):
                    continue
                job.update(state="running",started=time.time())
                agent.store.put("jobs",jid,job)
                self.health["running"]+=1
                self.execute(agent,jid)
                result=agent.store.get("jobs",jid)
                if result["state"]!="done":
                    raise RuntimeError("Worker finished without submitting through the provider SDK")
                self.health["done"]+=1
            except Exception as exc:
                job=agent.store.get("jobs",jid)
                # A successful SDK delivery wins even if the model's final message was interrupted.
                if job["state"]!="done":
                    job.update(state="failed",error=str(exc)[:500])
                    agent.store.put("jobs",jid,job)
                    self.health["failed"]+=1
            finally:
                self.health["running"]=max(0,self.health["running"]-1)
                with self.lock:
                    self.pending.discard((agent.url,jid))
                self.queue.task_done()

    def execute(self, agent, jid):
        job=agent.store.get("jobs",jid)
        if job["kind"]=="deliver":
            cd=card_digest(job["object"])
            with agent.provider._lock:
                rec=agent.store.get("cards",cd)
                if rec["state"]!="started":
                    raise RuntimeError("Contract is no longer open for production")
                if rec["card"]["signatures"]["4"]:
                    self.complete(agent,jid,{"alreadyDelivered":True})
                    return
            template=agent.provider.template(rec["card"]["templateDigest"])
            for step in agent.definition.get("steps",[]):
                current=agent.store.get("cards",cd)
                previous=next((r for r in current.get("steps",[]) if r["request"]["step"]==step["name"]),None)
                if previous is None:
                    agent.provider.request_step(cd,step["name"])
                    agent.provider.save_tasks()
                until=time.monotonic()+parse_duration(template["deadlines"]["authorizeWithin"]).total_seconds()
                while time.monotonic()<until and not self.stopping.is_set():
                    answer=next((r.get("answer") for r in agent.store.get("cards",cd).get("steps",[]) if r["request"]["step"]==step["name"]),None)
                    if answer:
                        if not answer.get("granted"):
                            raise RuntimeError("User declined step authorization")
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError("Step authorization timed out")
        if self.stub:
            result=({"price":agent.definition.get("pricing",{}).get("range",[10,30])[0]} if job["kind"]=="quote"
                    else agent.deliver(cd,agent.definition["sample"]))
            if job["kind"]=="deliver" and result.get("status")!="delivered" and not result.get("alreadyDelivered"):
                raise RuntimeError("Sample did not pass its contract verifier: "+str(result))
            self.complete(agent,jid,result)
            return
        folder=agent.home/"jobs"/jid
        folder.mkdir(parents=True,exist_ok=True)
        result=self.engine.run(job,agent.definition,folder,self.local_url+"/internal/tasks/"+agent.definition["id"]+"/"+jid,job["token"])
        agent.store.put("usage",jid,{**result,"elapsed":time.time()-job["started"]})

    @staticmethod
    def complete(agent,jid,result):
        job=agent.store.get("jobs",jid)
        job.update(state="done",result=result,finished=time.time())
        agent.store.put("jobs",jid,job)

    def close(self):
        self.stopping.set()
        self.engine.close()
        for thread in self.workers:
            thread.join(timeout=5)
        for agent in self.agents.values():
            agent.close()
        for account in self.accounts.values():
            account.close()
        if hasattr(self,'lease'): self.lease.close()


def create_app(host):
    async def boot(app):
        await asyncio.sleep(.3)
        try:
            await asyncio.to_thread(host.bootstrap,app)
        except Exception as exc:
            host.error=str(exc)

    async def maintenance():
        while True:
            await asyncio.sleep(.5)
            if not host.ready: continue
            for agent in list(host.agents.values()):
                try:
                    await asyncio.to_thread(agent.maintain)
                except Exception:
                    import logging
                    logging.getLogger('ioalab.services').exception('Provider maintenance failed; durable state retained')

    @asynccontextmanager
    async def lifespan(app):
        tasks=[asyncio.create_task(boot(app)),asyncio.create_task(maintenance())]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.to_thread(host.close)

    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)
    host.app=app

    @app.get("/health")
    def health():
        return {"ready":host.ready,"error":host.error,"execution":"sample" if host.stub else "swarm","services":len(host.agents),"queued":host.queue.qsize(),**host.health}

    def authorize(sid,jid,request):
        agent=host.agents.get("svc-"+sid)
        job=agent.store.get("jobs",jid) if agent else None
        supplied=request.headers.get("Authorization","").removeprefix("Bearer ")
        if not job or not secrets.compare_digest(supplied,job["token"]) or job["state"]!="running":
            raise HTTPException(403)
        return agent,job

    @app.get("/internal/tasks/{sid}/{jid}")
    def inspect(sid:str,jid:str,request:Request):
        agent,job=authorize(sid,jid,request)
        # Production receives requirements and generator guidance, never the fixture output.
        definition={k:v for k,v in agent.definition.items() if k not in ('sample','sample_spec','sample_inputs')}
        return {k:job[k] for k in ("kind","object","materials")}|{"definition":definition}

    @app.post("/internal/tasks/{sid}/{jid}/{operation}")
    def submit(sid:str,jid:str,operation:str,body:dict,request:Request):
        agent,job=authorize(sid,jid,request)
        if job["kind"]=="quote" and operation=="quote":
            price=body.get("price")
            import math
            if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<0:
                raise HTTPException(400,"A quote is a fixed nonnegative number")
            lo,hi=agent.definition.get("pricing",{}).get("range",[0,100])
            if not lo<=price<=hi:
                raise HTTPException(400,"Quote outside the published range")
            host.complete(agent,jid,{"price":price})
            return {"status":"quoted"}
        if job["kind"]!="deliver" or operation not in ("selftest","deliver"):
            raise HTTPException(400)
        cd=card_digest(job["object"])
        result=agent.selftest(cd,body) if operation=="selftest" else agent.deliver(cd,body)
        if result.get("status")=="delivered" or result.get("alreadyDelivered"):
            host.complete(agent,jid,result)
        return result

    return app


def main():
    import uvicorn,argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenario',choices=['nail-booking'])
    parser.add_argument('--only',help='Comma-separated service ids for a focused experiment')
    args=parser.parse_args()
    port=int(os.environ.get("IOALAB_PROVIDER_PORT","8751"))
    host=ServiceHost(platform=os.environ.get("IOALAB_BASE_URL",os.environ.get("IOALAB_PUBLIC_URL","http://127.0.0.1:8750")),
                     public_url=os.environ.get("IOALAB_PROVIDER_PUBLIC_URL",f"http://127.0.0.1:{port}"),
                     data=os.environ.get("IOALAB_PROVIDER_DATA",str(Path.home()/".ioalab-providers")),
                     stub=os.environ.get("IOALAB_STUB")=="1",only=args.only.split(",") if args.only else None,
                     concurrency=int(os.environ.get("IOALAB_PROVIDER_CONCURRENCY","2")),scenario=args.scenario)
    host.local_url=f"http://127.0.0.1:{port}"
    uvicorn.run(create_app(host),host=os.environ.get("IOALAB_PROVIDER_BIND","127.0.0.1"),port=port,log_level="warning")


if __name__=="__main__":
    main()
